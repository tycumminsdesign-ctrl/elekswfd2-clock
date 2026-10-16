// Runs argv[1..] as a child so macOS attributes its microphone use to this
// app bundle (WFD2Clock.app), which can ask for mic permission. A bare Python
// started by launchd cannot, and silently gets an all-zero signal instead.
#include <signal.h>
#include <spawn.h>
#include <stdio.h>
#include <sys/wait.h>

extern char **environ;
static pid_t child = 0;

static void forward(int sig) { if (child > 0) kill(child, sig); }

int main(int argc, char *argv[]) {
    if (argc < 2) { fprintf(stderr, "usage: wfd2-launcher program [args...]\n"); return 2; }
    signal(SIGTERM, forward);
    signal(SIGINT, forward);
    if (posix_spawn(&child, argv[1], NULL, NULL, &argv[1], environ) != 0) { perror("posix_spawn"); return 1; }
    int status = 0;
    while (waitpid(child, &status, 0) < 0) {}
    return WIFEXITED(status) ? WEXITSTATUS(status) : 1;
}
