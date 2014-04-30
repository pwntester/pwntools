__all__ = ['available', 'width', 'height', 'on_winch', 'output']

# we assume no terminal can display more lines than this
MAX_TERM_HEIGHT = 200

# default values
width = 80
height = 25
available = False

# list of callbacks triggered on SIGWINCH
on_winch = []

# if stdout and stderr are both TTY's then we assume it's the same one
# if we're running in a REPL all our fancy input/output magic will probably
# break something :'(
import sys
import pwn2 as __pwn__
fd = None
if not __pwn__.__hasrepl__:
    for f in [sys.stderr, sys.stdout]:
        if f.isatty():
            fd = f
            break

# unicode support?
import os
hasutf8 = 'UTF-8' in (os.getenv('LANG') or os.getenv('LC_MESSAGES') or
                      os.getenv('LC_ALL'))

if fd:
    available = True
    import atexit, struct, fcntl, re, signal, threading
    from termios import *
    settings = None

    def update_position ():
        global cursor_row, cursor_col

    def update_geometry ():
        global width, height
        hw = fcntl.ioctl(fd.fileno(), TIOCGWINSZ, '1234')
        height, width = struct.unpack('hh', hw)

    def handler_sigwinch (signum, stack):
        update_geometry()
        redraw()
        for cb in on_winch:
            cb()

    def handler_sigstop (signum, stack):
        resetterm()
        os.kill(os.getpid(), signal.SIGSTOP)

    def handler_sigcont (signum, stack):
        setupterm()
        redraw()

    def setupterm ():
        global settings
        update_geometry()
        do('civis') # hide cursor
        do('smkx') # keypad mode
        if not settings:
            settings = tcgetattr(fd.fileno())
        mode = tcgetattr(fd.fileno())
        IFLAG = 0
        OFLAG = 1
        CFLAG = 2
        LFLAG = 3
        ISPEED = 4
        OSPEED = 5
        CC = 6
        mode[IFLAG] = mode[IFLAG] & ~(BRKINT | ICRNL | INPCK | ISTRIP | IXON)
        mode[OFLAG] = mode[OFLAG] & ~(OPOST)
        mode[CFLAG] = mode[CFLAG] & ~(CSIZE | PARENB)
        mode[CFLAG] = mode[CFLAG] | CS8
        mode[LFLAG] = mode[LFLAG] & ~(ECHO | ICANON | IEXTEN)
        mode[CC][VMIN] = 1
        mode[CC][VTIME] = 0
        tcsetattr(fd, TCSAFLUSH, mode)

    def resetterm ():
        if settings:
            tcsetattr(fd.fileno(), TCSADRAIN, settings)
        do('cnorm')
        do('rmkx')
        fd.write(' \x08') # XXX: i don't know why this is needed...
                          #      only necessary when suspending the process

    def init ():
        setupterm()
        signal.signal(signal.SIGWINCH, handler_sigwinch)
        signal.signal(signal.SIGTSTP, handler_sigstop)
        signal.signal(signal.SIGCONT, handler_sigcont)
        # we start with one empty cell at the current cursor position
        put('\x1b[6n')
        s = ''
        while True:
            c = sys.stdin.read(1)
            s += c
            if c == 'R':
                break
        row, col = re.findall('\x1b\[(\d*);(\d*)R', s)[0]
        row = int(row) - height
        col = int(col) - 1
        cell = Cell()
        cell.start = (row, col)
        cell.end = (row, col)
        cell.content = []
        cell.frozen = True
        cell.float = 0
        cell.indent = 0
        cells.append(cell)
        # install wrappers for stdout and stderr
        class Wrapper:
            def __init__ (self, fd):
                self._fd = fd
            def write (self, s):
                output(s, frozen = True)
            def close (self):
                put('close\n')
                pass
            def __getattr__ (self, k):
                return self._fd.__getattribute__(k)
        if sys.stdout.isatty():
            sys.stdout = Wrapper(sys.stdout)
        if sys.stderr.isatty():
            sys.stderr = Wrapper(sys.stderr)
        # freeze all cells if an exception is thrown
        orig_hook = sys.excepthook
        def hook (*args):
            resetterm()
            for c in cells:
                c.frozen = True
                c.float = 0
            if orig_hook:
                orig_hook(*args)
            else:
                import traceback
                traceback.print_exception(*args)
            # this is a bit esoteric
            # look here for details: http://stackoverflow.com/questions
            # /12790328/how-to-silence-sys-excepthook-is-missing-error
            if fd.fileno() == 2:
                os.close(fd.fileno())
        sys.excepthook = hook

    def put (s):
        fd.write(s)

    def flush ():
        fd.flush()

    # terminal capabilities
    import curses
    curses.setupterm()
    capcache = {}
    def cap (c):
        s = capcache.get(c)
        if s:
            return s
        s = curses.tigetstr(c) or ''
        capcache[c] = s
        return s

    def do (c, *args):
        c = cap(c)
        if c:
            put(curses.tparm(c, *args))

    def goto ((r, c)):
        do('cup', r - scroll + height - 1, c)

    cells = []
    scroll = 0

    class Cell:
        pass

    class Handle:
        def __init__ (self, cell):
            self.h = id(cell)
        def update (self, s):
            update(self.h, s)
        def freeze (self):
            freeze(self.h)
        def delete (self):
            delete(self.h)

    STR, CSI, CRLF, BS, CR, SOH, STX = range(7)
    def parse_csi (buf, offset):
        i = offset
        while i < len(buf):
            c = buf[i]
            if c >= 0x40 and c < 0x80:
                break
            i += 1
        if i >= len(buf):
            return
        end = i
        cmd = [c, None, None]
        i = offset
        in_num = False
        args = []
        if buf[i] >= ord('<') and buf[i] <= ord('?'):
            cmd[1] = buf[i]
            i += 1
        while i < end:
            c = buf[i]
            if   c >= ord('0') and c <= ord('9'):
                if not in_num:
                    args.append(c - ord('0'))
                    in_num = True
                else:
                    args[-1] = args[-1] * 10 + c - ord('0')
            elif c == ord(';'):
                if not in_num:
                    args.append(None)
                in_num = False
                if len(args) > 16:
                    break
            elif c >= 0x20 and c <= 0x2f:
                cmd[2] = c
                break
            i += 1
        return cmd, args, end + 1

    def parse_utf8 (buf, offset):
        c0 = buf[offset]
        n = 0
        if   c0 & 0b11100000 == 0b11000000:
            n = 2
        elif c0 & 0b11110000 == 0b11100000:
            n = 3
        elif c0 & 0b11111000 == 0b11110000:
            n = 4
        elif c0 & 0b11111100 == 0b11111000:
            n = 5
        elif c0 & 0b11111110 == 0b11111100:
            n = 6
        if n:
            return offset + n

    def parse (s):
        out = []
        buf = map(ord, s)
        i = 0
        while True:
            if i >= len(buf):
                break
            x = None
            c = buf[i]
            if c >= 0x20 and c <= 0x7e:
                x = (STR, [chr(c)])
                i += 1
            elif c & 0xc0 and hasutf8:
                j = parse_utf8(buf, i)
                if j:
                    x = (STR, [''.join(map(chr, buf[i : j]))])
                    i = j
            elif c == 0x1b and len(buf) > i + 1 and buf[i + 1] == 0x5b:
                ret = parse_csi(buf, i + 2)
                if ret:
                    cmd, args, j = ret
                    x = (CSI, (cmd, args, ''.join(map(chr, buf[i : j]))))
                    i = j
            elif c == 0x01:
                x = (SOH, None)
                i += 1
            elif c == 0x02:
                x = (STX, None)
                i += 1
            elif c == 0x08:
                x = (BS, None)
                i += 1
            elif c == 0x09:
                x = (STR, '    ') # who the **** uses tabs anyway?
                i += 1
            elif c == 0x0a:
                x = (CRLF, None)
                i += 1
            elif c == 0x0d:
                if len(buf) > i + 1 and buf[i + 1] == 0x0a:
                    x = (CRLF, None)
                    i += 2
                else:
                    x = (CR, None)
                    i += 1
            if x is None:
                x = (STR, [c for c in '\\x%02x' % c])
                i += 1
            if x[0] == STR and out and out[-1][0] == STR:
                out[-1][1].extend(x[1])
            else:
                out.append(x)
        return out

    saved_cursor = None
    # XXX: render cells that is half-way on the screen
    def render_cell (cell):
        global scroll, saved_cursor
        row, col = cell.start
        row = row - scroll + height - 1
        if row < 0:
            return
        indent = min(cell.indent, width - 1)
        for t, x in cell.content:
            if   t == STR:
                i = 0
                while i < len(x):
                    if col >= width:
                        col = 0
                        row += 1
                        put('\r\n')
                    if col < indent:
                        put(' ' * (indent - col))
                        col = indent
                    c = x[i]
                    put(c)
                    col += 1
                    i += 1
            elif t == CSI:
                cmd, args, c = x
                put(c)
                # figure out if the cursor moved (XXX: here probably be bugs)
                if cmd[1] is None and cmd[2] is None:
                    c = cmd[0]
                    if len(args) >= 1:
                        n = args[0]
                    else:
                        n = None
                    if len(args) >= 2:
                        m = args[1]
                    else:
                        m = None

                    if   c == ord('A'):
                        n = n or 1
                        row = max(0, row - n)
                    elif c == ord('B'):
                        n = n or 1
                        row = min(height - 1, row + n)
                    elif c == ord('C'):
                        n = n or 1
                        col = min(width - 1, col + n)
                    elif c == ord('D'):
                        n = n or 1
                        col = max(0, col - n)
                    elif c == ord('E'):
                        n = n or 1
                        row = min(height - 1, row + n)
                        col = 0
                    elif c == ord('F'):
                        n = n or 1
                        row = max(0, row - n)
                        col = 0
                    elif c == ord('G'):
                        n = n or 1
                        col = min(width - 1, n)
                    elif c == ord('H') or c == ord('f'):
                        n = n or 1
                        m = m or 1
                        row = min(height - 1, n - 1)
                        col = min(width - 1, m - 1)
                    elif c == ord('S'):
                        n = n or 1
                        scroll += n
                        row = max(0, row - n)
                    elif c == ord('T'):
                        n = n or 1
                        scroll -= n
                        row = min(height - 1, row + n)
                    elif c == ord('s'):
                        saved_cursor = row, col
                    elif c == ord('u'):
                        if saved_cursor:
                            row, col = saved_cursor
            elif t == CRLF:
                if col <= width - 1:
                    put('\x1b[K') # clear line
                put('\r\n')
                col = 0
                row += 1
            elif t == BS:
                if col > 0:
                    put('\x08')
                    col -= 1
            elif t == CR:
                put('\r')
                col = 0
            elif t == SOH:
                put('\x01')
            elif t == STX:
                put('\x02')
            if row >= height:
                d = row - height + 1
                scroll += d
                row -= d
        row = row + scroll - height + 1
        cell.end = (row, col)

    def render_from (i, force = False):
        e = None
        goto(cells[i].start)
        for c in cells[i:]:
            if not force and c.start == e:
                goto(cells[-1].end)
                break
            elif e:
                c.start = e
            render_cell(c)
            e = c.end
        if e[0] < scroll or e[1] < width - 1:
            put('\x1b[J')
        flush()

    def redraw ():
        for i in reversed(range(len(cells))):
            row = cells[i].start[0]
            if row - scroll + height - 1 < 0:
                break
        # XXX: remove this line when render_cell is fixed
        if cells[i].start[0] - scroll + height < 0:
            i += 1
        render_from(i, force = True)

    lock = threading.Lock()
    def output (s = '', float = False, priority = 10, frozen = False,
                indent = 0):
        with lock:
            if float:
                float = priority
            cell = Cell()
            cell.content = parse(s)
            cell.frozen = frozen
            cell.float = float
            cell.indent = indent
            for i in reversed(range(len(cells))):
                if cells[i].float <= float:
                    break
            cell.start = cells[i].end
            i += 1
            cells.insert(i, cell)
            h = Handle(cell)
            if s == '':
                cell.end = cell.start
                return h
            # the invariant is that the cursor is placed after the last cell
            if i == len(cells) - 1:
                render_cell(cell)
                flush()
            else:
                render_from(i)
            return h

    def find_cell (h):
        for i, c in enumerate(cells):
            if id(c) == h:
                return i, c
        raise KeyError

    def discard_frozen ():
        # we assume that no cell will shrink very much and that noone has space
        # for more than MAX_TERM_HEIGHT lines in their terminal
        while len(cells) > 1 and scroll - cells[0].start[0] > MAX_TERM_HEIGHT:
            c = cells.pop(0)
            del c # trigger GC maybe, kthxbai

    def update (h, s):
        with lock:
            try:
                i, c = find_cell(h)
            except KeyError:
                return
            if not c.frozen and c.content <> s:
                c.content = parse(s)
                render_from(i)

    def freeze (h):
        try:
            i, c = find_cell(h)
            c.frozen = True
            c.float = 0
            if c.content == []:
                cells.pop(i)
            discard_frozen()
        except KeyError:
            return

    def delete (h):
        update(h, '')
        freeze(h)

    atexit.register(resetterm)
    init()

else:
    # stub
    class Handle:
        pass