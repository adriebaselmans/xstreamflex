"""Minimal stand-ins for Kodi's Python modules.

Enough to import and exercise the UI layer on a development machine. They record
what the add-on did so tests can assert on it. This is not an emulator — it catches
import errors, typos, wrong argument counts, and routing mistakes, which is the class
of bug that is otherwise only found by restarting Kodi.
"""
from __future__ import annotations

import sys
import types

LOGDEBUG, LOGINFO, LOGWARNING, LOGERROR = 0, 1, 2, 3
NOTIFICATION_INFO, NOTIFICATION_ERROR = "info", "error"
INPUT_ALPHANUM = 0
ALPHANUM_HIDE_INPUT = 64

log_lines = []
directory_items = []
resolved = []
notifications = []
textviewers = []


def reset():
    del log_lines[:], directory_items[:], resolved[:], notifications[:], textviewers[:]


class _InfoTag:
    def __init__(self):
        self.values = {}

    def __getattr__(self, name):
        if not name.startswith("set"):
            raise AttributeError(name)
        key = name[3:].lower()

        def setter(value):
            self.values[key] = value
        return setter


class ListItem:
    def __init__(self, label="", label2="", path="", offscreen=False):
        self.label = label
        self.path = path
        self.art = {}
        self.properties = {}
        self.mimetype = ""
        self._tag = _InfoTag()

    def getVideoInfoTag(self):
        return self._tag

    def setArt(self, art):
        self.art.update(art)

    def setProperty(self, key, value):
        self.properties[key] = value

    def getProperty(self, key):
        return self.properties.get(key, "")

    def setMimeType(self, mimetype):
        self.mimetype = mimetype

    def setContentLookup(self, value):
        self.properties["contentlookup"] = value

    def setPath(self, path):
        self.path = path


class Dialog:
    #: Queued answers: tests push what the user would type or pick.
    inputs = []
    selects = []
    yesnos = []

    def input(self, heading, defaultt="", **kwargs):
        return self.inputs.pop(0) if self.inputs else defaultt

    def select(self, heading, options, **kwargs):
        return self.selects.pop(0) if self.selects else -1

    def yesno(self, heading, message, **kwargs):
        return self.yesnos.pop(0) if self.yesnos else False

    def notification(self, heading, message, icon=None, time=0, sound=True):
        notifications.append((heading, message, icon))

    def textviewer(self, heading, text, usemono=False):
        textviewers.append((heading, text))


class DialogProgress:
    def __init__(self):
        self.percent = 0
        self.cancelled = False

    def create(self, heading, message=""):
        pass

    def update(self, percent, message=""):
        self.percent = percent

    def iscanceled(self):
        return self.cancelled

    def close(self):
        pass


class Player:
    playing = False

    def isPlaying(self):
        return self.playing

    def stop(self):
        Player.playing = False


class Monitor:
    def waitForAbort(self, timeout=0):
        return True

    def abortRequested(self):
        return True


class Addon:
    settings = {}
    info = {}

    def __init__(self, addon_id="plugin.video.xstreamflex"):
        self._id = addon_id

    def getAddonInfo(self, key):
        defaults = {
            "id": self._id, "name": "XstreamFlex", "path": "/addon",
            "profile": Addon.info.get("profile", "/tmp/profile"),
            "version": "0.1.0",
        }
        return Addon.info.get(key, defaults.get(key, ""))

    def getSetting(self, key):
        return Addon.settings.get(key, "")

    def setSetting(self, key, value):
        Addon.settings[key] = value

    def getLocalizedString(self, string_id):
        return "string-%d" % string_id

    def openSettings(self):
        pass


def install():
    """Register the stub modules in ``sys.modules``."""
    xbmc = types.ModuleType("xbmc")
    xbmc.LOGDEBUG, xbmc.LOGINFO = LOGDEBUG, LOGINFO
    xbmc.LOGWARNING, xbmc.LOGERROR = LOGWARNING, LOGERROR
    xbmc.log = lambda message, level=LOGINFO: log_lines.append((level, message))
    xbmc.Player = Player
    xbmc.Monitor = Monitor
    xbmc.executebuiltin = lambda command, wait=False: None
    xbmc.sleep = lambda ms: None

    xbmcgui = types.ModuleType("xbmcgui")
    xbmcgui.ListItem = ListItem
    xbmcgui.Dialog = Dialog
    xbmcgui.DialogProgress = DialogProgress
    xbmcgui.NOTIFICATION_INFO = NOTIFICATION_INFO
    xbmcgui.NOTIFICATION_ERROR = NOTIFICATION_ERROR
    xbmcgui.INPUT_ALPHANUM = INPUT_ALPHANUM
    xbmcgui.ALPHANUM_HIDE_INPUT = ALPHANUM_HIDE_INPUT

    xbmcplugin = types.ModuleType("xbmcplugin")
    xbmcplugin.setContent = lambda handle, content: directory_items.append(("content", content))
    xbmcplugin.addSortMethod = lambda handle, method: None
    xbmcplugin.addDirectoryItems = lambda handle, items, count=0: directory_items.extend(items)
    xbmcplugin.endOfDirectory = lambda handle, succeeded=True, updateListing=False, cacheToDisc=True: (
        directory_items.append(("end", succeeded))
    )
    xbmcplugin.setResolvedUrl = lambda handle, succeeded, listitem: resolved.append(
        (succeeded, listitem)
    )
    xbmcplugin.SORT_METHOD_LABEL = 1

    xbmcaddon = types.ModuleType("xbmcaddon")
    xbmcaddon.Addon = Addon

    xbmcvfs = types.ModuleType("xbmcvfs")
    xbmcvfs.translatePath = lambda path: path

    for module in (xbmc, xbmcgui, xbmcplugin, xbmcaddon, xbmcvfs):
        sys.modules[module.__name__] = module
