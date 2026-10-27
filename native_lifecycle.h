// VoiceGen's optional private timing pipe. No tensor inspection or GPU fences.
#pragma once
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <exception>
#include <unistd.h>

class VoiceGenLifecycle {
    using Steady = std::chrono::steady_clock;
    Steady::time_point clock_ = Steady::now();
    double started_ = wall();
    const char * action_;
    const char * scope_;
    bool finished_ = false;
    unsigned long serial_;
    static double wall() {
        return std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count();
    }
    void emit(const char * status, bool complete) noexcept {
        const char * configured = std::getenv("VOICEGEN_LIFECYCLE_FD");
        if (!configured) return;
        char * end = nullptr;
        long descriptor = std::strtol(configured, &end, 10);
        if (!end || *end || descriptor < 0) return;
        char line[768];
        const double elapsed = std::chrono::duration<double>(Steady::now()-clock_).count();
        int size;
        if (complete) {
            size = std::snprintf(line,sizeof(line),"{\"id\":\"%016lx%016lx\",\"action\":\"%s\",\"scope\":\"%s\",\"reason\":\"per_job\",\"started_at\":%.6f,\"ended_at\":%.6f,\"duration_seconds\":%.9f,\"status\":\"%s\"}\n",
                static_cast<unsigned long>(getpid()),serial_,action_,scope_,started_,wall(),elapsed,status);
        } else {
            size = std::snprintf(line,sizeof(line),"{\"id\":\"%016lx%016lx\",\"action\":\"%s\",\"scope\":\"%s\",\"reason\":\"per_job\",\"started_at\":%.6f,\"ended_at\":null,\"duration_seconds\":null,\"status\":\"running\"}\n",
                static_cast<unsigned long>(getpid()),serial_,action_,scope_,started_);
        }
        if (size > 0 && static_cast<size_t>(size) < sizeof(line)) {
            const auto ignored = ::write(static_cast<int>(descriptor),line,static_cast<size_t>(size));
            (void)ignored;
        }
    }
public:
    VoiceGenLifecycle(const char * action,const char * scope): action_(action),scope_(scope) {
        static unsigned long serial = 0;
        serial_ = ++serial;
        emit("running",false);
    }
    void finish() noexcept { if (!finished_) { finished_=true;emit("success",true); } }
    ~VoiceGenLifecycle() { if (!finished_) emit("error",true); }
};

template<class Model, class Session> class VoiceGenRelease {
    Model & model_; Session & session_;
public:
    VoiceGenRelease(Model & model,Session & session): model_(model),session_(session) {}
    ~VoiceGenRelease() {
        VoiceGenLifecycle event("unload","native_release");
        session_.reset();model_.reset();event.finish();
    }
};
