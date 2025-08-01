#include <signal.h>
#include <stdlib.h>
#include <stdio.h>
#include <unistd.h>
#include "rdma_server.h"

void handle_signal(int signal) {
    server_disconnect();
    exit(signal);
}

int main(int argc, char **argv)
{
    if(argc != 2) {
        fprintf(stderr, "Usage: %s <IP Address>\n", argv[0]);
        return 1;
    }

    signal(SIGINT, handle_signal);
    while(1)
    {
        printf("Start initializing the server\n");
        server_init(argv[1], MIND_DEFAULT_CONTROL_PORT);

        // wait for client to terminate
        while (check_cm_event() != RDMA_CM_EVENT_DISCONNECTED) {
            sleep(1);
        }
        printf("server Disconnecting and cleaning up\n");
        server_disconnect();
        server_release_buffer();
    }
    return 0;
}
