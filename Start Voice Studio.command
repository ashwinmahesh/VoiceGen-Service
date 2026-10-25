#!/bin/zsh
cd -- "${0:A:h}" || exit 1
make start && make open
