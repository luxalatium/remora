#include "remora_state.h"

static int write_value(const char *directory, int value)
{
    FILE *file = remora_open_state(directory, "state.tmp", "w");
    if (!file) return 1;
    int failed = fprintf(file, "%d\n", value) < 0;
    return fclose(file) != 0 || failed;
}

static int read_value(const char *directory, int expected)
{
    FILE *file = remora_open_state(directory, "state.tmp", "r");
    if (!file) return 1;
    int actual = 0;
    int valid = fscanf(file, "%d", &actual) == 1 && actual == expected;
    fclose(file);
    return !valid;
}

int main(int argc, char **argv)
{
    if (argc != 3) return 2;
    FILE *absent = remora_open_state(argv[2], "state.tmp", "r");
    if (absent) { fclose(absent); return 3; }
    if (write_value(argv[1], 7) || write_value(argv[2], 11)) return 4;
    if (read_value(argv[1], 7) || read_value(argv[2], 11)) return 5;
    puts("PASS: separate run directories preserve independent state");
    return 0;
}
