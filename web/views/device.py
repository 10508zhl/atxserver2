# coding: utf-8
#

import json

from logzero import logger
from tornado.web import authenticated

from ..database import db, jsondate_loads, time_now
from ..utils import jsondate_dumps
from .base import BaseRequestHandler, BaseWebSocketHandler, AuthRequestHandler


class OccupyError(Exception):
    pass


class ReleaseError(Exception):
    pass


class APIUserDeviceHandler(BaseRequestHandler):
    """ device Acquire and Release """

    async def post(self):
        data = self.get_payload()
        udid = data["udid"]
        try:
            await occupy_device(self.current_user.email, udid)
            self.write_json({
                "success": True,
                "description": "Device successfully added"
            })
        except OccupyError as e:
            self.set_status(403)  # forbidden
            self.write_json({
                "success": False,
                "description": "Device add failed: " + str(e),
            })

    async def delete(self, udid: str):
        try:
            await release_device(self.current_user.email, udid)
            self.write_json({
                "success": True,
                "description": "Device successfully released"
            })
        except ReleaseError as e:
            self.set_status(403)  # forbidden
            self.write_json({
                "success": False,
                "description": "Device release failed: " + str(e),
            })


class DeviceControlHandler(BaseRequestHandler):
    """ device remote control """

    async def get(self, udid):
        self.render("remotecontrol.html")


class DeviceItemHandler(BaseRequestHandler):
    async def put(self, id):
        pass

    async def get_json(self, id):
        data = await db.table("devices").get(id)
        if not data:
            self.set_status(400)  # bad request
            self.write_json({
                "success": False,
                "description": "device not found " + id,
            })
            return

        address = None
        priority = 0
        for s in data.get('sources', {}).values():
            if s['priority'] > priority:
                address = s['deviceAddress']
                priority = s['priority']

        data['address'] = address
        self.write_json({
            "success": True,
            "data": data,
        })

    async def get_html(self, id):
        self.write("No such html")


class DeviceListHandler(BaseRequestHandler):
    """ Device List will show in first page """

    @authenticated
    async def get(self):
        """ get data from database """
        if self.get_argument('json', None) is not None:
            self.write_json({
                "success": True,
                "data": {
                    "devices": await db.table("devices").get_all(limit=50, desc="createdAt"),
                    "count": await db.table("devices").count(),
                }
            })  # yapf: disable
            return
        self.render("index.html")

    async def post(self):
        """ add data into database """
        data = jsondate_loads(self.request.body)
        id = await db.table("devices").save(data)
        self.write_json({"id": id, "data": data})


class DeviceChangesWSHandler(BaseWebSocketHandler):
    def write_json(self, data):
        self.ws_connection.write_message(jsondate_dumps(data))

    async def open(self):
        self.__opened = True
        conn, feed = await db.table("devices").watch()
        with conn:
            while await feed.fetch_next():
                if not self.__opened:
                    break
                data = await feed.next()
                logger.info("feed data: %s", data)
                self.write_json({
                    "event": "insert" if data['old_val'] is None else 'update',
                    "data": data['new_val'],
                })  # yapf: disable

    def on_message(self, msg):
        logger.debug("receive message %s", msg)
        pass

    def on_close(self):
        self.__opened = False
        print("Websocket closed")


async def occupy_device(email: str, udid: str):
    """
    Raises:
        OccupyError
    """
    device = await db.device.get(udid)
    if not device:
        raise OccupyError("device not exist")
    if not device.get('present'):
        raise OccupyError("device absent")
    if device.get('using'):
        if device.get('userId') == email:
            # already used by ..{email}
            return
        raise OccupyError("device busy")

    ret = await db.device.update({"using": True}, udid)
    if ret['skipped'] == 1:
        raise OccupyError(
            "not fast enough, device have been taken from others")
    await db.device.update({"userId": email, "usingBebanAt": time_now()}, udid)


async def release_device(email: str, udid: str):
    device = await db.device.get(udid)
    if not device:
        raise ReleaseError("device not exist")
    if device.get('userId') != email:
        raise ReleaseError("device is not owned by you")
    await db.device.update({"using": False}, udid)


class DeviceBookWSHandler(BaseWebSocketHandler):
    """ 连接成功时占用，断开时释放设备 """

    async def open(self, udid):
        if not self.current_user:
            self.write_message("need to login")
            self.close()
            return

        email = self.current_user.email
        try:
            await occupy_device(email, udid)
        except OccupyError as e:
            self.write_message(str(e))
            self.close()
            return

        self.write_message("device is yours")
