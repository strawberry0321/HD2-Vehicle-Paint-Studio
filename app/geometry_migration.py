"""Keep historical part/triangle references when physical running gear is split."""
import copy
def migrate(state,catalog):
 if state.get('physicalRevision',1)>=2:return state
 state=copy.deepcopy(state)
 for record in state.get('layers',[])+state.get('surfaceColors',[]):
  moves=catalog.get('physicalRemaps',{}).get(record['unit'],[])
  for key in ['surfaces']:
   if key in record:record[key]=remap(record[key],moves)
  if record.get('projection',{}).get('mask'):record['projection']['mask']=remap(record['projection']['mask'],moves)
 state['physicalRevision']=2
 return state
def remap(surfaces,moves):
 result=[]
 for surface in surfaces:
  move=next((m for m in moves if m['fromPart']==surface['part']),None)
  if not move:result.append(surface);continue
  index={old:new for new,old in enumerate(move['triangles'])};keep=[t for t in surface['triangles'] if t not in index];shift=[index[t] for t in surface['triangles'] if t in index]
  if keep:result.append(dict(part=surface['part'],triangles=keep))
  if shift:result.append(dict(part=move['part'],triangles=shift))
 return result
