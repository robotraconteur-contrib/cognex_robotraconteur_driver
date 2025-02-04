# -*- coding: utf-8 -*-

# Simple example Robot Raconteur Industrial Cognex service

import RobotRaconteur as RR
RRN = RR.RobotRaconteurNode.s
import RobotRaconteurCompanion as RRC
from RobotRaconteurCompanion.Util.UuidUtil import UuidUtil
from RobotRaconteurCompanion.Util.IdentifierUtil import IdentifierUtil
from RobotRaconteurCompanion.Util.GeometryUtil import GeometryUtil
from RobotRaconteurCompanion.Util.SensorDataUtil import SensorDataUtil
from RobotRaconteurCompanion.Util.InfoFileLoader import InfoFileLoader
from RobotRaconteurCompanion.Util.AttributesUtil import AttributesUtil
from RobotRaconteurCompanion.Util.RobDef import register_service_types_from_resources
from RobotRaconteurCompanion.Util.ImageUtil import ImageUtil
import drekar_launch_process
import argparse
import numpy as np
import socket
import threading
import traceback
import copy
import time
import os
import signal
import select

from ._native_client import native_exec_command, native_read_image
import re
import cv2

host = '0.0.0.0'  # IP address of PC
port = 3000


def multisplit(s, delims):
    pos = 0
    for i, c in enumerate(s):
        if c in delims:
            yield s[pos:i]
            pos = i + 1
    yield s[pos:]


class sensor_impl(object):
    def __init__(self, object_sensor_info, cognex_addr, cognex_pw):

        self.object_recognition_sensor_info = object_sensor_info
        self.device_info = object_sensor_info.device_info
        self.cognex_addr = cognex_addr
        self.cognex_pw = cognex_pw

        # threading setting
        self._lock = threading.RLock()
        self._running = False

        # utils
        self._uuid_util = UuidUtil(RRN)
        self._identifier_util = IdentifierUtil(RRN)
        self._geometry_util = GeometryUtil(RRN)
        self._sensor_data_util = SensorDataUtil(RRN)
        self._image_util = ImageUtil(RRN)

        # initialize objrecog types
        self._object_recognition_sensor_data_type = RRN.GetStructureType(
            "com.robotraconteur.objectrecognition.ObjectRecognitionSensorData")
        self._recognized_objects_type = RRN.GetStructureType("com.robotraconteur.objectrecognition.RecognizedObjects")

        self._recognized_object_type = RRN.GetStructureType("com.robotraconteur.objectrecognition.RecognizedObject")
        self._named_pose_cov_type = RRN.GetStructureType("com.robotraconteur.geometry.NamedPoseWithCovariance")
        self._named_pose_type = RRN.GetStructureType("com.robotraconteur.geometry.NamedPose")

        # initialize detection obj map
        self._detection_obj_type = RRN.NewStructure("edu.robotraconteur.cognexsensor.detection_obj")

        self._seqno = 0
        self._detected_objects = None
        self._wires_init = False

    def RRServiceObjectInit(self, ctx, service_path):
        self._wires_init = True

    def start(self):
        self._running = True
        self._camera = threading.Thread(target=self._object_update)
        self._camera.daemon = True
        self._camera.start()

    def close(self):
        self._running = False
        try:
            self.c.close()
        except:
            pass
        self._camera.join()

    def parse_sensor_string(self, string_data):

        recognized_objects = []
        detected_objects = {}

        string_data = string_data.split('{')  # find leading text
        object_list = string_data[-1].split(";")  # split different object info in string
        object_list.pop(0)

        for i in range(len(object_list)):  					# split the data from cognex and parse to RR object
            general = object_list[i].split(":")
            name = general[0]
            if '#ERR' not in general[1]:  # if detected

                info = list(filter(None, multisplit(general[1], '(),=°\r\n')))
                # standard type

                recognized_object = self._recognized_object_type()
                recognized_object.recognized_object = self._identifier_util.CreateIdentifierFromName(name)
                named_pose = self._named_pose_type()
                named_pose.pose = self._geometry_util.xyz_rpy_to_pose(
                    [float(info[0]) / 1000., float(info[1]) / 1000., 0.0], [0.0, 0.0, np.deg2rad(float(info[2]))])
                cov_pose = self._named_pose_cov_type()
                cov_pose.pose = named_pose
                recognized_object.pose = cov_pose
                if len(info) > 3:
                    recognized_object.confidence = float(info[3]) / 100.
                else:
                    recognized_object.confidence = 1.0
                recognized_objects.append(recognized_object)

                # my type
                detected_object = self._detection_obj_type
                detected_object.name = name
                detected_object.x = float(info[0]) / 1000.
                detected_object.y = float(info[1]) / 1000.
                detected_object.angle = float(info[2])
                detected_object.detected = True
                detected_objects[name] = detected_object

        recognized_objects_sensor_data = self._object_recognition_sensor_data_type()
        recognized_objects_sensor_data.sensor_data = self._sensor_data_util.FillSensorDataHeader(
            self.device_info, self._seqno)
        recognized_objects_sensor_data.recognized_objects = self._recognized_objects_type()
        recognized_objects_sensor_data.recognized_objects.recognized_objects = recognized_objects

        self._seqno += 1

        return recognized_objects_sensor_data, detected_objects

    def _object_update(self):

        connected = False
        self.c = None
        string_buf_rem = ""

        while self._running:

            if not connected:
                string_buf_rem = ""
                try:
                    self.c = socket.create_connection(self.cognex_addr)
                    connected = True
                    print("Connected to Cognex sensor")
                except:
                    time.sleep(0.5)
                    continue

            if not self._running:
                break

            try:
                # Use select to wait for data
                ready = select.select([self.c], [], [self.c], None)
                string_data = self.c.recv(1024).decode("utf-8")
                if len(string_data) == 0:
                    if connected:
                        connected = False
                        try:
                            self.c.close()
                        except:
                            pass
                        print("Warning: Connection to Cognex sensor lost")
            except:
                if connected:
                    connected = False
                    try:
                        self.c.close()
                    except:
                        pass
                    print("Warning: Connection to Cognex sensor lost")
                time.sleep(0.5)
                continue

            if not self._running:
                break

            # print(f"Received data: {string_data}")

            string_data1 = string_buf_rem + string_data
            string_data2 = string_data1.splitlines(keepends=True)

            if string_data2[-1][-1] not in ['\n', '\r']:
                string_buf_rem = string_data2[-1]
                string_data2.pop(-1)

            if (len(string_data2) == 0):
                continue

            string_data = string_data2[-1].strip()

            try:
                object_recognition_sensor_data, detection_objects = self.parse_sensor_string(string_data)

                with self._lock:
                    self._detected_objects = object_recognition_sensor_data.recognized_objects

                if self._wires_init:
                    # pass to RR wire
                    self.detection_wire.OutValue = detection_objects
                    # pass to RR pipe
                    self.object_recognition_sensor_data.SendPacket(object_recognition_sensor_data)
            except:
                traceback.print_exc()

        try:
            self.c.close()
        except:
            pass

    def capture_recognized_objects(self):
        with self._lock:
            if self._detected_objects is None:
                ret = self._recognized_objects_type()
                ret.recognized_objects = []
                return ret
            return copy.deepcopy(self._detected_objects)

    def cognex_get_cell(self, cell):
        if not re.match(r'^[A-Z][0-9]{3}$', cell):
            raise ValueError("Invalid cell number")
        return native_exec_command(self.cognex_addr[0], self.cognex_pw, f"GV{cell}", True).decode('utf-8')

    def cognex_set_cell_int(self, cell, value):
        if not re.match(r'^[A-Z][0-9]{3}$', cell):
            raise ValueError("Invalid cell number")
        return native_exec_command(self.cognex_addr[0], self.cognex_pw, f"SI{cell}{value}", False)

    def cognex_set_cell_float(self, cell, value):
        if not re.match(r'^[A-Z][0-9]{3}$', cell):
            raise ValueError("Invalid cell number")
        return native_exec_command(self.cognex_addr[0], self.cognex_pw, f"SF{cell}{value:.4f}", False)

    def cognex_set_cell_string(self, cell, value):
        if not re.match(r'^[A-Z][0-9]{3}$', cell):
            raise ValueError("Invalid cell number")
        # check for allowed characters
        if not re.match(r'^[\x20-\x7E]+$', value):
            raise ValueError("Invalid characters in string")
        return native_exec_command(self.cognex_addr[0], self.cognex_pw, f"SS{cell}{value}", False)

    def cognex_trigger_acquisition(self):
        return native_exec_command(self.cognex_addr[0], self.cognex_pw, "SW8", False)

    def cognex_trigger_event(self, event):
        event = int(event)
        if event < 0 or event > 8:
            raise ValueError("Invalid event number")
        return native_exec_command(self.cognex_addr[0], self.cognex_pw, f"SW{event}", False)

    def cognex_capture_image(self):
        bmp_bytes = native_read_image(self.cognex_addr[0], self.cognex_pw)

        img = cv2.imdecode(np.frombuffer(bmp_bytes, np.uint8), cv2.IMREAD_COLOR)

        return self._image_util.array_to_image(img, 'bgr888')


def main():

    parser = argparse.ArgumentParser(description="Cognex Sensor Robot Raconteur Driver")

    parser.add_argument("--sensor-info-file", type=argparse.FileType('r'), default=None,
                        required=True, help="Cognex sensor info file (required)")
    parser.add_argument("--cognex-host", type=str, required=True,
                        help="Cognex sensor IP address or hostname (required)")
    parser.add_argument("--cognex-port", type=int, default=3000, help="Cognex sensor port (default 3000)")
    parser.add_argument("--cognex-password", type=str, default="",
                        help="Cognex sensor password for native mode commands (default (empty))")

    args, _ = parser.parse_known_args()

    cognex_addr = (args.cognex_host, args.cognex_port)
    cognex_pw = args.cognex_password

    RRC.RegisterStdRobDefServiceTypes(RRN)
    register_service_types_from_resources(RRN, __package__, ['edu.robotraconteur.cognexsensor.robdef'])

    with args.sensor_info_file:
        sensor_info_text = args.sensor_info_file.read()

    info_loader = InfoFileLoader(RRN)
    sensor_info, sensor_ident_fd = info_loader.LoadInfoFileFromString(sensor_info_text,
                                                                      "com.robotraconteur.objectrecognition.ObjectRecognitionSensorInfo", "device")

    attributes_util = AttributesUtil(RRN)
    sensor_attributes = attributes_util.GetDefaultServiceAttributesFromDeviceInfo(sensor_info.device_info)

    with RR.ServerNodeSetup("cognex_Service", 59901) as node_setup:

        cognex_inst = sensor_impl(sensor_info, cognex_addr, cognex_pw)
        cognex_inst.start()

        ctx = RRN.RegisterService("cognex", "edu.robotraconteur.cognexsensor.CognexSensor", cognex_inst)
        ctx.SetServiceAttributes(sensor_attributes)

        print("Cognex Service Started")
        print()
        print("Candidate connection urls:")
        ctx.PrintCandidateConnectionURLs()
        print()
        print("Press Ctrl-C to quit...")

        drekar_launch_process.wait_exit()

        cognex_inst.close()
