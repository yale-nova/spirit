from pymemcache.client.base import Client

client = Client('localhost:8001')
client.set('some_key', 'some_value')
result = client.get('some_key')

print(result.decode('utf-8'))
