#!/usr/bin/env bash
# sysstat recipes preserve DFLAGS feature definitions while overriding CFLAGS.
recipe_prepare() {
    local option
    tp_base_flags cflags "$(tool_path cc)" "$C_PROBE" .c || return 1
    if [[ -z $(jq -r .flags.cflags "$CONFIG") ]]; then
        tp_default_flag cflags "$(tool_path cc)" -O2 || return 1
    fi
    for option in -Wall -Wstrict-prototypes -pipe; do
        tp_default_flag cflags "$(tool_path cc)" "$option" || return 1
    done
}
recipe_build() {
    local cflags cppflags ldflags build_env=()
    cflags=$(jq -r .flags.cflags "$CONFIG")
    cppflags=$(jq -r .flags.cppflags "$CONFIG")
    ldflags=$(jq -r .flags.ldflags "$CONFIG")
    build_env=(env -u MAKEFLAGS -u MFLAGS -u MAKEOVERRIDES -u CONFIG_SITE "CC=$(tool_path cc)" "CFLAGS=$cflags" "CPPFLAGS=$cppflags" "LDFLAGS=$ldflags")
    run "$SOURCE" "$WORK/logs" mpstat-configure 180 "${build_env[@]}" "$(tool_path bash)" ./configure || return 1
    cflags=$(tp_make_flags "$cppflags $cflags") || return 1
    ldflags=$(tp_make_flags "$ldflags") || return 1
    run "$SOURCE" "$WORK/logs" mpstat-make 240 "${build_env[@]}" "$(tool_path make)" -j1 "CFLAGS=$cflags" "LFLAGS=$ldflags" mpstat || return 1
    mkdir -p -- "$STAGE/bin" && cp -p -- "$SOURCE/mpstat" "$STAGE/bin/mpstat"
}
