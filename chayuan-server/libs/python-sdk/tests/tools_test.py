from open_chatcaht.chayuan_api import Chayuan

client = Chayuan()
print(client.tool.list())
print(client.tool.call('calculate', {"text": "3+5/2"}))