#!/usr/bin/env python
"""
Drive through notifications client interactions.
"""
import json
import sys
import textwrap
import threading
import time

import requests
import websocket_client
from tornado import ioloop


def print_request(request):
    lines = ['%s %s' % (request.method, request.full_url)]
    lines.extend(sorted(': '.join(item) for item in request.headers.items()))
    if request.data:
        lines.extend(['', '',
                      '&'.join(sorted('='.join(x) for x in request.data))])
    print '::', '\n:: '.join(lines), '\n'


def print_response(response):
    lines = ['HTTP/1.1 %s' % response.status_code]
    lines.extend(sorted(': '.join(item) for item in response.headers.items()))
    if response.content:
        lines.extend(['', '', response.content])
    print '::', '\n:: '.join(lines), '\n'


def wait(seconds, test, sleep=1):
    count = 0
    while not test() and count < seconds:
        time.sleep(sleep)
        count += 1


class WebSocket(websocket_client.WebSocket):

    def __init__(self, url, token):
        super(WebSocket, self).__init__(url)
        self.token = token
        self.messages = []
        self.is_open = False

    def on_open(self):
        self.is_open = True
        self.write_message('token: ' + self.token)

    def write_message(self, data):
        print '>>', data, '\n'
        super(WebSocket, self).write_message(data)

    def on_message(self, data):
        print '>>', data, '\n'
        self.messages.append(json.loads(data))


def step(desc):
    # Indent the text in a numbered paragraph.
    if desc[0].isdigit():
        joiner = '\n' + ' ' * (1 + desc.index(' '))
    else:
        joiner = '\n'
    print '\n\n', joiner.join(textwrap.wrap(desc, 72)), '\n', '=' * 40


def main(api_url):
    # This is our local store of push URLs keyed by domain.
    queues = {}

    http = requests.session(hooks={'pre_request': print_request,
                                   'response': print_response})

    step('1. Get a token. This is how we identify ourselves to the service.')
    r = http.post(api_url + '/token/')
    assert r.status_code == 200
    response = json.loads(r.content)
    token, user_queue = response['token'], response['queue']
    print 'Token:', token, '\n'

    step('2. Sync push URLs. If we were a browser, we\'d want our push URLs '
         'to date with the user\'s other clients.')
    r = http.get(api_url + '/queue/', params={'token': token})
    assert r.status_code == 200
    assert json.loads(r.content) == queues  # No push URLs yet.

    step('4. Get a list of socket servers')
    r = http.get(api_url + '/nodes/')
    assert r.status_code == 200
    nodes = json.loads(r.content)['nodes']
    print 'WebSocket nodes:', nodes

    step('5. Try connecting to a socket server.')
    print 'We identify ourselves on the websocket by sending the token.\n'
    ws = WebSocket('ws://' + nodes[0], token)
    wait(10, lambda: ws.is_open)
    print 'Connected.'

    step('6. Get a new push URL. If we were in a browser, we\'d be doing this '
         'on behalf of a web site.')
    domain = 'example.com'
    r = http.post(api_url + '/queue/', {'token': token, 'domain': domain})
    assert r.status_code == 200
    queues[domain] = json.loads(r.content)['queue']

    print 'This is where the client would return the push URL to the website.'

    step('7. Listen for messages coming from the socket server.')
    print 'Sending fake messages.\n'
    r = http.post(queues[domain], {'title': 'message one', 'body': 'ok'})
    assert r.status_code == 200
    r = http.post(queues[domain], {'title': 'message two', 'body': 'ok'})
    assert r.status_code == 200
    r = http.post(queues[domain], {'title': 'message three', 'body': 'ok'})
    assert r.status_code == 200

    # Wait for convergence.
    if len(ws.messages) != 3:
        print 'Waiting on the websocket...\n'
        wait(10, lambda: len(ws.messages) == 3)

    assert len(ws.messages) == 3
    print 'Got the messages on the websocket.'

    step('8. Get stored messages.')
    print 'Check that all the messages are there.\n'
    r = http.get(user_queue, params={'token': token})
    assert r.status_code == 200
    assert json.loads(r.content)['messages'] == ws.messages

    print 'We can get messages based on timestamp.\n'
    ts = ws.messages[1]['timestamp']
    r = http.get(user_queue, params={'token': token, 'since': ts})
    assert r.status_code == 200
    assert json.loads(r.content)['messages'] == ws.messages[1:]

    print 'Check that our push URLs are availble over HTTP (for syncing).\n'
    r = http.get(api_url + '/queue/', params={'token': token})
    assert r.status_code == 200
    assert json.loads(r.content) == queues

    step('9. Tell the server to mark messages as read after user action.')
    print 'Clients would send this after they see the user read a message.\n'
    r = http.post(user_queue,
                  {'action': 'read', 'key': ws.messages[0]['key']})
    assert r.status_code == 200

    step('10. Mark messages as read when the server notifies us.')
    print 'We can see which messages were read over the websocket.\n'
    # Wait for convergence.
    if len(ws.messages) != 4:
        print 'Waiting on the websocket...\n'
        wait(10, lambda: len(ws.messages) == 4)
    print ws.messages[-1], '\n'

    print 'And if we check over HTTP we\'ll see the new activity.\n'
    # Get the timestamp of the message before the read marker.
    ts = ws.messages[-2]['timestamp']
    r = http.get(user_queue, params={'token': token, 'since': ts})
    assert r.status_code == 200
    messages = json.loads(r.content)['messages']
    assert len(messages) == 2  # The last message + the read marker.
    # We see the message we sent.
    assert messages[-1]['body'] == {'read': ws.messages[0]['key']}
    # The websocket and HTTP formats match.
    assert messages[-1] == ws.messages[-1]

    step('11. Revoke push URLs after user action.')
    r = http.delete(queues[domain], params={'token': token})
    assert r.status_code == 200

    r = http.post(queues[domain], {'title': 'message one', 'body': 'ok'})
    assert r.status_code == 404

    print 'All good!'


if __name__ == '__main__':
    # Start the tornado IO loop in another thread.
    io_thread = threading.Thread(target=ioloop.IOLoop.instance().start)
    io_thread.start()

    try:
        main(*sys.argv[1:])
    finally:
        # Kill the IO loop.
        ioloop.IOLoop.instance().stop()
        io_thread.join()
