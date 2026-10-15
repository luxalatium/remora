#ifndef REMORA_STATE_H
#define REMORA_STATE_H

#include <errno.h>
#include <stdio.h>

/* The caller supplies a unique run directory through the existing tmpdir CLI.
 * Keep state local to that directory; never fall back to shared global state. */
static FILE *remora_open_state(const char *directory, const char *name,
                               const char *mode)
{
    char path[4096];
    int length = snprintf(path, sizeof(path), "%s/%s", directory, name);
    if (length < 0 || (size_t)length >= sizeof(path)) {
        errno = ENAMETOOLONG;
        return NULL;
    }
    return fopen(path, mode);
}

#endif
