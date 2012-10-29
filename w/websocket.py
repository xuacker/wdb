from log_colorizer import get_color_logger
import array
import base64
import hashlib
import socket
import struct
import sys

log = get_color_logger('w-socket')

OPCODES = ['continuation', 'text', 'binary',
           '?', '?', '?', '?', '?',
           'close', 'ping', 'pong',
           '?', '?', '?', '?', '?']


class WsHeader(object):
    expected_fields = {
        'Host': 'host',
        'Upgrade': 'upgrade',
        'Connection': 'connection',
        'Sec-WebSocket-Key': 'key',
        'Sec-WebSocket-Version': 'version',
    }
    optional_fields = {
        'Origin': 'origin',
        'Sec-WebSocket-Protocol': 'protocol',
        'Sec-WebSocket-Extensions': 'extensions'
    }

    def __init__(self, header):
        assert header[-4:] == '\r\n\r\n'
        lines = header[:-4].split('\r\n')
        self.method, self.path, self.http = lines[0].split(' ')
        self._fields = {}
        for line in lines[1:-1]:
            key, value = line.split(': ')
            self._fields[key] = value
        for key, name in self.expected_fields.items():
            assert key in self._fields
            setattr(self, name, self._fields[key])

        for key, name in self.optional_fields.items():
            setattr(self, name, self._fields.get(key, None))


class WsFrame(object):

    @staticmethod
    def from_socket(get):
        frame = WsFrame()

        def unpack(format, bytes):
            log.debug('Got bytes %r' % bytes)
            return struct.unpack(format, bytes)

        header, payload = unpack("BB", get(2))

        frame.fin = header & 0x80
        frame.opcode = header & 0x0f
        frame.type = OPCODES[frame.opcode]
        assert payload & 0x80  # Masking key

        frame.payload_len = payload & 0x7f
        if frame.payload_len == 0x7e:
            frame.payload_len = unpack('!H', get(2))
        elif frame.payload_len == 0x7f:
            frame.payload_len = unpack('!Q', get(8))

        frame.mask = array.array("B", get(4))
        frame.data = array.array("B", get(frame.payload_len))
        for i in xrange(len(frame.data)):
            frame.data[i] = frame.data[i] ^ frame.mask[i % 4]
        return frame

    @staticmethod
    def from_data(data):
        frame = WsFrame()

        frame.fin = 0x80  # Final frame
        frame.type = 'text'
        frame.opcode = OPCODES.index(frame.type)  # Text
        header = struct.pack("B", frame.fin | frame.opcode)

        payload_len = len(data)
        if payload_len < 126:
            payload = struct.pack("B", payload_len)
        elif payload_len <= 0xFFFF:
            payload = struct.pack("!BH", 126, payload_len)
        else:
            payload = struct.pack("!BQ", 127, payload_len)

        frame.data = header + payload + data
        return frame

    @staticmethod
    def close():
        frame = WsFrame()

        frame.fin = 0x80  # Final frame
        frame.type = 'close'
        frame.opcode = OPCODES.index(frame.type)  # Text
        header = struct.pack("B", frame.fin | frame.opcode)
        payload = struct.pack("B", 0)

        frame.data = header + payload
        return frame


class WsPacket(object):

    def __init__(self, peer, send=None, close=False):
        self.close = False
        if not send and not close:  # Receive mode
            frame = WsFrame.from_socket(peer.recv)
            self.data = frame.data
            while not frame.fin and not frame.type == 'close':
                frame = WsFrame.from_socket(peer.recv)
                self.data += frame.data
            if frame.type == 'close':
                self.close = True
            else:
                self.data = self.data.tostring().decode("utf-8")

        elif send:  # Send mode
            self.data = send.encode("utf-8")
            peer.send(WsFrame.from_data(self.data).data)

        elif close:
            peer.send(WsFrame.close().data)


class WebSocket(object):

    def __init__(self, host, port):
        log.info('Creating websocket on %s:%d' % (host, port))
        self.host = host
        self.port = port
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.handshaken = False
            self.sock.bind((self.host, self.port))
            self.sock.listen(5)
        except socket.error:
            log.warn('Port %d is already taken' % port)
            self.status = 'FAIL'
        else:
            log.debug('Listening on %s:%d' % (host, port))
            self.status = 'OK'

    def handshake(self, header):
        sha1 = hashlib.sha1()
        sha1.update(header.key)
        sha1.update("258EAFA5-E914-47DA-95CA-C5AB0DC85B11")
        return (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Accept: %s\r\n"
            "\r\n"
        ) % base64.b64encode(sha1.digest())

    def recv_header(self):
        h = ''
        while h.find('\r\n\r\n') == -1:
            h += self.peer.recv(16)
        return WsHeader(h)

    def wait_for_connect(self):
        self.peer, self.info = self.sock.accept()
        log.debug('Handshaking with peer %r' % self.peer)
        header = self.recv_header()
        self.peer.send(self.handshake(header))
        log.debug('Handshaken')

    def receive(self):
        log.debug('Receiving')
        packet = WsPacket(self.peer)
        log.debug('Received packet')
        if packet.close:
            WsPacket(self.peer, close=True)
            return 'CLOSED'
        return packet.data

    def send(self, data):
        log.debug('Sending packet')
        WsPacket(self.peer, send=data)
        log.debug('Sent')

    def close(self):
        log.debug('Try Closing')
        WsPacket(self.peer, close=True)
        log.debug('Closing')
        WsPacket(self.peer)
        self.peer.close()
        log.debug('Closed')


if __name__ == '__main__':
    print "Connecting to : localhost:%s" % sys.argv[1]
    ws = WebSocket('localhost', int(sys.argv[1]))
    print "Waiting for connect"
    ws.wait_for_connect()
    print "Connected !"
    print "Waiting for data"
    data = ''
    while data != 'CLOSED':
        data = ws.receive()
        print data
        ws.send(data)
        if data == 'abort':
            ws.close()
            break
