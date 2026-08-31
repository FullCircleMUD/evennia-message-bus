import evennia
from evennia_message_bus import MessageType, register, get_instance_id


class TestMessage(MessageType):
    kind = "test"           # required, unique across the bus
    timeout = 30                      # seconds; defaults to 10
    payload_keys = ("message",)    # required keys, checked before sending

    def handle(self, message) -> bool:
        text = message.payload["message"]
        evennia.SESSION_HANDLER.announce_all(text)
        if not "RESPONSE" in text:
            self.send(message.from_instance, {"message": "RESPONSE : " + message.payload["message"]})
        return True                   # True = done, False = not yet

register(TestMessage)