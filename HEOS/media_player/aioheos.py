#!/usr/bin/env python3
" Heos python lib "

import asyncio
import sys
import json
from datetime import datetime
from concurrent.futures import CancelledError
from . import aioheosupnp
import logging

_LOGGER = logging.getLogger('heos')

HEOS_PORT = 1255

GET_PLAYERS = 'player/get_players'
GET_PLAYER_INFO = 'player/get_player_info'
GET_PLAY_STATE = 'player/get_play_state'
SET_PLAY_STATE = 'player/set_play_state'
GET_MUTE_STATE = 'player/get_mute'
SET_MUTE_STATE = 'player/set_mute'
GET_VOLUME = 'player/get_volume'
SET_VOLUME = 'player/set_volume'
GET_NOW_PLAYING_MEDIA = 'player/get_now_playing_media'
GET_QUEUE = 'player/get_queue'
CLEAR_QUEUE = 'player/clear_queue'
PLAY_NEXT = 'player/play_next'
PLAY_PREVIOUS = 'player/play_previous'
PLAY_QUEUE = 'player/play_queue'
TOGGLE_MUTE = 'player/toggle_mute'

GET_GROUPS = 'group/get_groups'

GET_MUSIC_SOURCES = 'browser/get_music_sources'
BROWSE = 'browser/browse'

PLAYER_VOLUME_CHANGED = 'event/player_volume_changed'
PLAYER_STATE_CHANGED = 'event/player_state_changed'
PLAYER_NOW_PLAYING_CHANGED = 'event/player_now_playing_changed'
PLAYER_NOW_PLAYING_PROGRESS = 'event/player_now_playing_progress'

SYSTEM_PRETTIFY = 'system/prettify_json_response'
SYSTEM_REGISTER_FOR_EVENTS = 'system/register_for_change_events'

class AioHeosException(Exception):
    " AioHeosException class "
    # pylint: disable=super-init-not-called
    def __init__(self, message):
        self.message = message


class AioHeos(object): # pylint: disable=too-many-public-methods,too-many-instance-attributes
    " Asynchronous Heos class "

    def __init__(self, loop, speakername=None, host=None, verbose=False):
        self._speakername = speakername
        self._host = host
        self._loop = loop
        self._players = None
        self._play_state = None
        self._mute_state = None
        self._volume_level = 0
        self._current_position = 0
        self._current_position_updated_at = 0
        self._duration = 0
        self._media_artist = None
        self._media_album = None
        self._media_title = None
        self._media_image_url = None
        self._media_id = None

        self._verbose = verbose
        self._player_id = None
        self._upnp = aioheosupnp.AioHeosUpnp(loop=loop, verbose=verbose)
        self._reader = None
        self._writer = None
        self._subscribtion_task = None

    @asyncio.coroutine
    def ensure_player(self):
        """ ensure player """
        # timeout after 10 sec
        for _ in range(0, 20):
            self.request_players()
            if self.player_id is None:
                yield from asyncio.sleep(0.5)
            else:
                return

    @staticmethod
    def _url_to_addr(url):
        import re
        addr = re.search('https?://([^:/]+)[:/].*$', url)
        if addr:
            return addr.group(1)
        else:
            return None

    @asyncio.coroutine
    def connect(self, host=None, port=HEOS_PORT, callback=None):
        """ setup proper connection """
        if host is not None:
            self._host = host

        # discover
        if not self._host:
            url = yield from self._upnp.discover()
            self._host = self._url_to_addr(url)

        # connect
        if self._verbose:
            _LOGGER.info(self._host + ': ' + '[I] Connecting to {}:{}'.format(self._host, port))
        yield from self._connect(self._host, port)

        # please, do not prettify json
        self.register_pretty_json(False)
        # and get events
        self.register_for_change_events()

        # setup subscription loop
        if self._subscribtion_task is None:
            self._subscribtion_task = self._loop.create_task(
                self._async_subscribe(callback))

        # request for players
        yield from self.ensure_player()

        # request info
        if self.player_id:
            self.request_now_playing_media()
            self.request_play_state()
            self.request_volume()

    @asyncio.coroutine
    def _connect(self, host, port=HEOS_PORT):
        " connect "
        while True:
            try:
                # pylint: disable=line-too-long
                self._reader, self._writer = yield from asyncio.open_connection(host, port, loop=self._loop)
                return
            except TimeoutError:
                print('[E] Connection timed out, will try {}:{} again...'.format(self._host, port))
            except: # pylint: disable=bare-except
                print('[E]', sys.exc_info()[0])

            yield from asyncio.sleep(5.0)

    def send_command(self, command, message=None):
        " send command "
        msg = 'heos://' + command
        if message:
            if 'pid' in message.keys() and message['pid'] is None:
                message['pid'] = self.player_id
            msg += '?' + '&'.join("{}={}".format(key, val) for (key, val) in message.items())
        msg += '\r\n'
        if self._verbose:
            _LOGGER.info(self._host + ': ' + 'COMMAND: ' + msg)
        self._writer.write(msg.encode('ascii'))

    @staticmethod
    def _parse_message(message):
        " parse message "
        try:
            return dict(elem.split('=') for elem in message.split('&'))
        except ValueError as exc:
            _LOGGER.info('[E] parsing message ({}), {}.'.format(message, exc))
        return {}

    def _dispatcher(self, command, payload):
        " call parser functions "
        # if self._verbose:
        if self._verbose:
            _LOGGER.info(self._host + ': ' + 'DISPATCHER: ' + str((command, payload)))

        if 'pid' in payload and str(payload['pid']) != str(self._player_id):
            _LOGGER.info(self._host + ': ' + 'DISPATCHER: playerID doesn"t match, ignoring ' + str(self._player_id) + ' ' + str(payload['pid']))
            return

        callbacks = {
            GET_PLAYERS: self._parse_players,
            GET_PLAY_STATE: self._parse_play_state,
            SET_PLAY_STATE: self._parse_play_state,
            GET_MUTE_STATE: self._parse_mute_state,
            SET_MUTE_STATE: self._parse_mute_state,
            GET_VOLUME: self._parse_volume,
            SET_VOLUME: self._parse_volume,
            GET_NOW_PLAYING_MEDIA: self._parse_now_playing_media,
            PLAYER_VOLUME_CHANGED: self._parse_player_volume_changed,
            PLAYER_STATE_CHANGED: self._parse_player_state_changed,
            PLAYER_NOW_PLAYING_CHANGED: self._parse_player_now_playing_changed,
            PLAYER_NOW_PLAYING_PROGRESS: self._parse_player_now_playing_progress,
            }
        commands_ignored = (
            SYSTEM_PRETTIFY,
            )
        if command in callbacks.keys():
            callbacks[command](payload)
        elif command in commands_ignored:
            if self._verbose:
                _LOGGER.info(self._host + ': ' + '[I] command %s is ignored.', str(command))
        else:
            _LOGGER.info(self._host + ': ' + '[W] command %s is not handled.', str(command))

    def _parse_command(self, data):
        " parse command "
        try:
            data_heos = data['heos']
            command = data_heos['command']
            if 'result' in data_heos.keys() and data_heos['result'] == 'fail':
                raise AioHeosException(data_heos['message'])
            if 'payload' in data.keys():
                self._dispatcher(command, data['payload'])
            elif 'message' in data_heos.keys():
                if data_heos['message'] == 'command under process':
                    return None
                message = self._parse_message(data_heos['message'])
                self._dispatcher(command, message)
            else:
                raise AioHeosException('No message or payload in reply.')
        # pylint: disable=bare-except
        except AioHeosException as exc:
            raise AioHeosException('Problem parsing ({})'.format(exc))
        except:
            raise AioHeosException('Problem parsing command.')

        return None

    @asyncio.coroutine
    def _callback_wrapper(self, callback): # pylint: disable=no-self-use
        if callback:
            try:
                yield from callback()
            except: # pylint: disable=bare-except
                pass

    @asyncio.coroutine
    def _async_subscribe(self, callback=None): # pylint: disable=too-many-branches
        """ event loop """
        while True:
            if self._reader is None:
                yield from asyncio.sleep(0.1)
                continue
            try:
                msg = yield from self._reader.readline()
            except TimeoutError:
                _LOGGER.info(self._host + ': ' + '[E] Connection got timed out, try to reconnect...')
                yield from self._connect(self._host)
            except ConnectionResetError:
                _LOGGER.info(self._host + ': ' + '[E] Peer reset our connection, try to reconnect...')
                yield from self._connect(self._host)
            except (GeneratorExit, CancelledError):
                _LOGGER.info(self._host + ': ' + '[I] Cancelling event loop...')
                return
            except: # pylint: disable=bare-except
                _LOGGER.info(self._host + ': ' + '[E] Ignoring ' + sys.exc_info()[0])
            #if self._verbose:
            #    _LOGGER.info(self._host + ': ' + msg.decode())
            # simplejson doesnt need to decode from byte to ascii
            data = json.loads(msg.decode())
            if self._verbose:
                _LOGGER.info(self._host + ': ' + 'DATA: ' + str(data))
            try:
                self._parse_command(data)
            except AioHeosException as exc:
                _LOGGER.info(self._host + ': ' + '[E] ' + str(exc))
                if self._verbose:
                    _LOGGER.info(self._host + ': ' + 'MSG - ' + str(msg))
                    _LOGGER.info(self._host + ': ' + 'MSG decoded - ' + str(msg.decode()))
                    _LOGGER.info(self._host + ': ' + 'MSG json - ' + str(data))
                continue
            if callback:
                if self._verbose:
                    _LOGGER.info(self._host + ': ' + 'TRIGGER CALLBACK')
                self._loop.create_task(self._callback_wrapper(callback))

    def close(self):
        " close "
        _LOGGER.info(self._host + ': ' + '[I] Closing down...')
        if self._subscribtion_task:
            self._subscribtion_task.cancel()

    def register_for_change_events(self):
        " register for change events "
        self.send_command(SYSTEM_REGISTER_FOR_EVENTS, {'enable': 'on'})

    def register_pretty_json(self, enable=False):
        " register for pretty json "
        set_enable = 'off'
        if enable is True:
            set_enable = 'on'
        self.send_command(SYSTEM_PRETTIFY, {'enable': set_enable})

    def request_players(self):
        " get players "
        self.send_command(GET_PLAYERS)

    def _parse_players(self, payload):
        self._players = payload
        for player in self._players:
            if player['name'] == self._speakername:
                self._player_id = player['pid']
                break

    @property
    def player_id(self):
        " get player id "
        return self._player_id

    def request_player_info(self):
        " request player info "
        self.send_command(GET_PLAYER_INFO, {'pid': self.player_id})

    def request_play_state(self):
        " request play state "
        self.send_command(GET_PLAY_STATE, {'pid': self.player_id})

    def _parse_play_state(self, payload):
        self._play_state = payload['state']
        # A stopped device is shows in HASS as powered off and can't be controlled via UI
        # But the reality is that sending a 'play' command to it wakes it up and playback resumes
        # So, treat stopped as paused
        if self._play_state == 'stop':
            self._play_state = 'pause'

    def get_play_state(self):
        """ get play state """
        return self._play_state

    def request_mute_state(self):
        " request mute state "
        self.send_command(GET_MUTE_STATE, {'pid': self.player_id})

    def _parse_mute_state(self, payload):
        self._mute_state = payload['state']

    def get_mute_state(self):
        """ get mute state """
        return self._mute_state

    def request_volume(self):
        " request volume "
        self.send_command(GET_VOLUME, {'pid': self.player_id})

    def set_volume(self, volume_level):
        " set volume "
        if volume_level > 100:
            volume_level = 100
        if volume_level < 0:
            volume_level = 0
        self.send_command(SET_VOLUME, {'pid': self.player_id,
                                       'level': volume_level})

    def _parse_volume(self, message):
        self._volume_level = message['level']

    def get_volume(self):
        """ get volume """
        return self._volume_level

    def volume_level_up(self, step=10):
        " volume level up "
        self.set_volume(self._volume_level + step)

    def volume_level_down(self, step=10):
        " volume level down "
        self.set_volume(self._volume_level - step)

    def _set_play_state(self, state):
        " set play state "
        if state not in ('play', 'pause', 'stop'):
            AioHeosException('Not an accepted play state {}.'.format(state))

        self.send_command(SET_PLAY_STATE, {'pid': self.player_id,
                                           'state': state})

    def stop(self):
        " stop player "
        self._set_play_state('stop')

    def play(self):
        " play "
        self._set_play_state('play')

    def pause(self):
        " pause "
        self._set_play_state('pause')

    def request_now_playing_media(self):
        " get playing media "
        self.send_command(GET_NOW_PLAYING_MEDIA, {'pid': self.player_id})

    def _parse_now_playing_media(self, payload):
        if 'artist' in payload.keys():
            self._media_artist = payload['artist']
        if 'album' in payload.keys():
            self._media_album = payload['album']
        if 'song' in payload.keys():
            self._media_title = payload['song']
        if 'image_url' in payload.keys():
            self._media_image_url = payload['image_url']
        if 'mid' in payload.keys():
            self._media_id = payload['mid']

    def get_media_artist(self):
        """ get media artist """
        return self._media_artist

    def get_media_album(self):
        """ get media album """
        return self._media_album

    def get_media_song(self):
        """ get media song """
        return self._media_title

    def get_media_image_url(self):
        """ get media image url """
        return self._media_image_url

    def get_media_title(self):
        """ get media title """
        return self._media_title

    def get_media_id(self):
        """ get media id """
        return self._media_id

    def get_position(self):
        """ get position """
        return self._current_position

    def get_position_updated_at(self):
        """ get position update at """
        return self._current_position_updated_at

    def get_duration(self):
        """ get duration """
        return self._duration

    def request_queue(self):
        " request queue "
        self.send_command(GET_QUEUE, {'pid': self.player_id})

    def clear_queue(self):
        " clear queue "
        self.send_command(CLEAR_QUEUE, {'pid': self.player_id})

    def request_play_next(self):
        " play next "
        self.send_command(PLAY_NEXT, {'pid': self.player_id})

    def _parse_play_next(self, payload):
        " parse play next "
        pass

    def request_play_previous(self):
        " play prev "
        self.send_command(PLAY_PREVIOUS, {'pid': self.player_id})

    def play_queue(self, qid):
        " play queue "
        self.send_command(PLAY_QUEUE, {'pid': self.player_id,
                                       'qid': qid})

    def request_groups(self):
        " get groups "
        self.send_command(GET_GROUPS)

    def toggle_mute(self):
        " toggle mute "
        self.send_command(TOGGLE_MUTE, {'pid': self.player_id})

    def request_music_sources(self):
        " get music sources "
        self.send_command(GET_MUSIC_SOURCES, {'range': '0,29'})

    def request_browse_source(self, sid):
        " browse source "
        self.send_command(BROWSE, {'sid': sid, 'range': '0,29'})

    def play_content(self, content, content_type='audio/mpeg'):
        """ play content """
        self._loop.create_task(self._upnp.play_content(content, content_type))
        # asyncio.wait([task])

    def _parse_player_volume_changed(self, message):
        self._mute_state = message['mute']
        self._volume_level = int(message['level'])

    def _parse_player_state_changed(self, message):
        self._play_state = message['state']
        # A stopped device is shows in HASS as powered off and can't be controlled via UI
        # But the reality is that sending a 'play' command to it wakes it up and playback resumes
        # So, treat stopped as paused
        if self._play_state == 'stop':
            self._play_state = 'pause'

    def _parse_player_now_playing_changed(self, _): # pylint: disable=invalid-name
        " event / now playing changed, request what changed. "
        self.request_now_playing_media()

    def _parse_player_now_playing_progress(self, message): # pylint: disable=invalid-name
        self._current_position = int(message['cur_pos'])
        self._current_position_updated_at = datetime.utcnow()
        self._duration = int(message['duration'])