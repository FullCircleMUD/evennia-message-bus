r"""
Evennia settings file.

The available options are found in the default settings file found
here:

https://www.evennia.com/docs/latest/Setup/Settings-Default.html

Remember:

Don't copy more from the default file than you actually intend to
change; this will make sure that you don't overload upstream updates
unnecessarily.

When changing a setting requiring a file system path (like
path/to/actual/file.py), use GAME_DIR and EVENNIA_DIR to reference
your game folder and the Evennia library folders respectively. Python
paths (path.to.module) should be given relative to the game's root
folder (typeclasses.foo) whereas paths within the Evennia library
needs to be given explicitly (evennia.foo).

If you want to share your game dir, including its settings, you can
put secret game- or server-specific settings in secret_settings.py.

"""

# Use the defaults from Evennia unless explicitly overridden
from evennia.settings_default import *

######################################################################
# Evennia base server config
######################################################################

# This is the name of your game. Make it catchy!
SERVERNAME = "game-b"

MESSAGEBUS_INSTANCE_ID = SERVERNAME

INSTALLED_APPS += ["evennia_message_bus"]


DATABASES["messagebus"] = {
    "ENGINE": "django.db.backends.sqlite3",
    "NAME": os.path.join(GAME_DIR, "server", "messagebus.db3"),
}

_BUS_ROUTER = "evennia_message_bus.db_router.MessageBusRouter"

DATABASE_ROUTERS = list(globals().get("DATABASE_ROUTERS", []))
if _BUS_ROUTER not in DATABASE_ROUTERS:
    DATABASE_ROUTERS.append(_BUS_ROUTER)


# Shift every port by 100 so both instances can run at once
TELNET_PORTS = [4100]
WEBSERVER_PORTS = [(4101, 4105)]
WEBSOCKET_CLIENT_PORT = 4102
AMP_PORT = 4106


######################################################################
# Settings given in secret_settings.py override those in this file.
######################################################################
try:
    from server.conf.secret_settings import *
except ImportError:
    print("secret_settings.py file not found or failed to import.")
