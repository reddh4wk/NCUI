CC ?= gcc
PREFIX ?= /usr
DESTDIR ?=
BINDIR ?= $(PREFIX)/bin
MANDIR ?= $(PREFIX)/share/man/man1
LICENSEDIR ?= $(PREFIX)/share/licenses/nc-matrix
TARGET ?= nc-matrix
OPT_FLAGS ?= -O3 -march=native -flto

PKG_DEPS := gtk+-3.0 gtk-layer-shell-0 pangocairo epoxy ncursesw gio-unix-2.0 wayland-client wayland-egl egl
PKG_CFLAGS := $(shell pkg-config --cflags $(PKG_DEPS))
PKG_LIBS := $(shell pkg-config --libs $(PKG_DEPS))

ALL_CFLAGS := $(OPT_FLAGS) $(CFLAGS) -std=c11 -Wall -Wextra -Werror=implicit-function-declaration $(PKG_CFLAGS)
ALL_LDFLAGS := $(OPT_FLAGS) $(LDFLAGS) $(PKG_LIBS) -lm -lpthread

.PHONY: all install uninstall clean

all: $(TARGET)

$(TARGET): nc-matrix.c
	@mkdir -p $(dir $@)
	$(CC) $(ALL_CFLAGS) $< -o $@ $(ALL_LDFLAGS)

install: $(TARGET)
	install -Dm755 $(TARGET) $(DESTDIR)$(BINDIR)/nc-matrix
	install -Dm644 nc-matrix.1 $(DESTDIR)$(MANDIR)/nc-matrix.1
	install -Dm644 LICENSE $(DESTDIR)$(LICENSEDIR)/LICENSE

uninstall:
	rm -f $(DESTDIR)$(BINDIR)/nc-matrix
	rm -f $(DESTDIR)$(MANDIR)/nc-matrix.1
	rm -rf $(DESTDIR)$(LICENSEDIR)

clean:
	rm -f $(TARGET) nc-matrix
