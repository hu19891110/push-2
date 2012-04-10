from argparse import ArgumentParser

import tornado.ioloop
import tornado.web
import tornado.websocket
import zmq
from zmq.eventloop import ioloop, zmqstream

from mozsvc.config import load_into_settings


SOCKETS = {}


class SocketHandler(tornado.websocket.WebSocketHandler):

    def on_message(self, message):
        if message.startswith('token: '):
            self.token = message.split(' ', 1)[-1]
            SOCKETS[self.token] = self

    def close(self):
        del SOCKETS[self.token]


application = tornado.web.Application([
    ('.*', SocketHandler),
])


class Push(object):

    def __init__(self, stream):
        stream.on_recv(self.recv)

    def recv(self, msg):
        key, token, data = msg
        self.send(token, data)

    def send(self, token, data):
        if token in SOCKETS:
            try:
                SOCKETS[token].write_message(data)
            except Exception:
                del SOCKETS[token]


def main():
    parser = ArgumentParser('Pubsub listener pushing to websockets.')
    parser.add_argument('config', help='path to the config file')
    args, settings = parser.parse_args(), {}
    load_into_settings(args.config, settings)
    config = settings['config']

    ioloop.install()
    socket = zmq.Context().socket(zmq.SUB)
    socket.connect(config.get('zeromq', 'sub'))
    socket.setsockopt(zmq.SUBSCRIBE, 'PUSH')
    print 'SUB socket on', config.get('zeromq', 'sub')

    loop = ioloop.IOLoop.instance()
    websockets = config.get_map('websockets')
    Push(zmqstream.ZMQStream(socket, loop))
    application.listen(websockets['port'])
    loop.start()


if __name__ == '__main__':
    main()