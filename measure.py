import sys
sys.path.insert(0, '/home/administrator/factcheckdao/.venv/lib/python3.14/site-packages')

from genlayer_py.abi.calldata import encode
import rlp

src = open('/home/administrator/factcheckdao/contracts/factcheck_dao.py','rb').read()
calldata = encode({})
payload = rlp.encode([src, calldata])

print('src:', len(src))
print('calldata:', len(calldata))
print('payload bytes:', len(payload))
print('payload hex chars:', len(payload)*2)
