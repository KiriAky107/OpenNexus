"""Closed facts from one rejected original comparison; never timeout admission."""
BOUND_MS=36000
POINTS=('BEFORE_OWNER','AFTER_OWNER','AFTER_SCENE','AFTER_FINAL_OWNER','SCENE_FALSE_AT_DEADLINE')


def validate(value,ready):
    keys={'schema','point','ready_elapsed_ms','previous_elapsed_ms','sampled_elapsed_ms','deadline_elapsed_ms',
          'state_observed','original_guard_rejected','diagnostic_only','native_timeout_verified'}
    if not (type(value) is dict and set(value)==keys and type(ready) is int and 0<=ready<2**63-BOUND_MS and
            type(value['schema']) is int and value['schema']==1 and type(value['point']) is str and value['point'] in POINTS and
            value['original_guard_rejected'] is True and value['diagnostic_only'] is True and value['native_timeout_verified'] is False):
        return False
    if not all(type(value[k]) is int and 0<=value[k]<2**63 for k in
               ('ready_elapsed_ms','previous_elapsed_ms','sampled_elapsed_ms','deadline_elapsed_ms')):
        return False
    previous,sampled,deadline=(value[k] for k in ('previous_elapsed_ms','sampled_elapsed_ms','deadline_elapsed_ms'))
    if not (value['ready_elapsed_ms']==ready and deadline==ready+BOUND_MS and ready<=previous<=deadline):
        return False
    point,observed=value['point'],value['state_observed']
    if point in ('BEFORE_OWNER','AFTER_OWNER'):
        if observed is not None:return False
    elif point=='AFTER_SCENE':
        if type(observed) is not bool:return False
    elif point=='AFTER_FINAL_OWNER':
        if observed is not True:return False
    else:
        return observed is False and sampled==deadline
    return sampled<previous or sampled>deadline
