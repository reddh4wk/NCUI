CC ?= gcc
PKG_DEPS := gtk+-3.0 gtk-layer-shell-0 pangocairo epoxy ncursesw gio-unix-2.0 wayland-client wayland-egl egl
PKG_CFLAGS := $(shell pkg-config --cflags $(PKG_DEPS))
PKG_LIBS := $(shell pkg-config --libs $(PKG_DEPS))

CFLAGS := -O3 -march=native -flto -fopenmp -std=c11 -Wall -Wextra -Werror=implicit-function-declaration $(PKG_CFLAGS)
LDFLAGS := -O3 -march=native -flto -fopenmp $(PKG_LIBS) -lm -lpthread

TARGET := ../../bin/ncmatrix

.PHONY: all clean

all: $(TARGET)

$(TARGET): ncmatrix.c
	@mkdir -p $(dir $@)
	$(CC) $(CFLAGS) $< -o $@ $(LDFLAGS)

clean:
	rm -f $(TARGET)
