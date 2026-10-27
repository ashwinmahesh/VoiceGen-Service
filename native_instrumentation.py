"""Reproducible, bounded instrumentation of the pinned native CLI source."""
from pathlib import Path
import subprocess


def instrumented_cli(original):
    replacements = (
        ('#include "args.h"', '#include "args.h"\n#include "voicegen_lifecycle.h"'),
        ('minitts::app::print_build_info(std::cout);', 'minitts::app::print_build_info(std::cout);\n            std::cout << "voicegen_lifecycle=1\\n";'),
        ('        auto model = registry.load(load_request);', '        VoiceGenLifecycle load_event("load","native_weights");\n        auto model = registry.load(load_request);'),
        ('        auto session = model->create_task_session(task_spec, session_options);',
         '        auto session = model->create_task_session(task_spec, session_options);\n        load_event.finish();\n        VoiceGenRelease release_event(model,session);'),
    )
    for old,new in replacements:
        if original.count(old)!=1:raise RuntimeError('Pinned native lifecycle instrumentation no longer matches the reviewed source')
        original=original.replace(old,new)
    return original


def prepare_native(source, project):
    source,project=Path(source),Path(project)
    original=subprocess.check_output(['git','-C',str(source),'show','HEAD:app/cli/main.cpp'],text=True)
    expected=instrumented_cli(original)
    main=source/'app/cli/main.cpp';header=source/'app/cli/voicegen_lifecycle.h'
    canonical=(project/'native_lifecycle.h').read_text()
    changed=subprocess.check_output(['git','-C',str(source),'status','--porcelain','--untracked-files=all'],text=True).splitlines()
    allowed={' M app/cli/main.cpp','?? app/cli/voicegen_lifecycle.h'}
    if any(line not in allowed for line in changed) or main.read_text() not in (original,expected) or (header.exists() and header.read_text()!=canonical):
        raise RuntimeError('Native source has unrelated local changes; preserve them before rebuilding. Only the reviewed VoiceGen lifecycle instrumentation can be reused.')
    main.write_text(expected);header.write_text(canonical)
