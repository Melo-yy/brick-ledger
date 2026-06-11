"""Quick database test with multi-user support."""
import database as db

db.init_db()

# Create test user
uid = db.create_user('test', 'pbkdf2:sha256:600000$fakehash')
print(f'Created user id: {uid}')

# Create order for user
oid = db.create_order(uid, {'model': 'Test', 'size': '42', 'expense': 100})
print(f'Created order id: {oid} for user {uid}')

# List orders
orders, total = db.list_orders(uid)
print(f'User {uid} has {total} orders')

# Cleanup
db.delete_order(uid, oid)
print('Test passed!')
