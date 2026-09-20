# dummy_glide.py
from pydbus import SessionBus
from gi.repository import GLib

BUS = SessionBus()
LOOP = GLib.MainLoop()

class Glide:
    dbus = """<node>
      <interface name='org.example.Glide'>
        <method name='Update'>
          <arg type='d' name='x' direction='in'/>
          <arg type='d' name='y' direction='in'/>
          <arg type='t' name='ms' direction='in'/>
        </method>
      </interface>
    </node>"""
    def Update(self, x, y, ms): print("recv:", x, y, ms)

BUS.request_name("org.example.Glide")
BUS.publish("org.example.Glide", ("/glide", Glide()))
LOOP.run()
