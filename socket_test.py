import numpy as np
import socket, threading, traceback, copy, time, os, signal


host = '169.254.235.13'		#IP address of PC, align with Server Host Name in Insight TCP/IP Communication 
port = 3000
s=socket.socket()
s.bind((host, port))
s.listen(5)
c,addr=s.accept()

while True:
	string_data = c.recv(1024).decode("utf-8") 
	print(string_data)