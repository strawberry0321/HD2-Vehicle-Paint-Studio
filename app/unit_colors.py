"""Per-Unit overrides preserve historical shared colors during migration."""
def effective(state,uid,gid):
 return state.get('unitColors',{}).get(uid,{}).get(gid,state['groups'][gid])

def for_unit(state,uid):
 import copy
 result=copy.copy(state)
 result['groups']={gid:effective(state,uid,gid) for gid in state['groups']}
 return result
