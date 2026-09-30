#include <errno.h>
#include <fcntl.h>
#include <linux/input.h>
#include <linux/uinput.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

static int fd = -1;

static int emit(unsigned short type, unsigned short code, int value) {
    struct input_event ev;
    memset(&ev, 0, sizeof(ev));
    ev.type = type;
    ev.code = code;
    ev.value = value;
    return write(fd, &ev, sizeof(ev)) == (ssize_t)sizeof(ev) ? 0 : -1;
}

static int sync_events(void) {
    return emit(EV_SYN, SYN_REPORT, 0);
}

static void cleanup(void) {
    if (fd >= 0) {
        emit(EV_KEY, BTN_MIDDLE, 0);
        sync_events();
        ioctl(fd, UI_DEV_DESTROY);
        close(fd);
        fd = -1;
    }
}

int main(void) {
    fd = open("/dev/uinput", O_WRONLY | O_NONBLOCK);
    if (fd < 0) {
        perror("open /dev/uinput");
        return 1;
    }

    if (ioctl(fd, UI_SET_EVBIT, EV_KEY) < 0 ||
        ioctl(fd, UI_SET_KEYBIT, BTN_MIDDLE) < 0 ||
        ioctl(fd, UI_SET_EVBIT, EV_REL) < 0 ||
        ioctl(fd, UI_SET_RELBIT, REL_X) < 0 ||
        ioctl(fd, UI_SET_RELBIT, REL_Y) < 0) {
        perror("uinput capabilities");
        cleanup();
        return 1;
    }

    struct uinput_setup setup;
    memset(&setup, 0, sizeof(setup));
    setup.id.bustype = BUS_USB;
    setup.id.vendor = 0x1209;
    setup.id.product = 0x0001;
    setup.id.version = 1;
    snprintf(setup.name, UINPUT_MAX_NAME_SIZE, "Mirai Gesture Mouse");

    if (ioctl(fd, UI_DEV_SETUP, &setup) < 0 || ioctl(fd, UI_DEV_CREATE) < 0) {
        perror("uinput create");
        cleanup();
        return 1;
    }

    atexit(cleanup);

    char line[128];
    while (fgets(line, sizeof(line), stdin)) {
        if (line[0] == 'D') {
            emit(EV_KEY, BTN_MIDDLE, 1);
            sync_events();
        } else if (line[0] == 'U') {
            emit(EV_KEY, BTN_MIDDLE, 0);
            sync_events();
        } else if (line[0] == 'M') {
            int dx = 0, dy = 0;
            if (sscanf(line + 1, "%d %d", &dx, &dy) == 2) {
                if (dx) emit(EV_REL, REL_X, dx);
                if (dy) emit(EV_REL, REL_Y, dy);
                sync_events();
            }
        } else if (line[0] == 'Q') {
            break;
        }
    }

    return 0;
}
