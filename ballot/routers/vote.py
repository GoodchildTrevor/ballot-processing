def _check_cross_nomination_conflict(nominees, nominations):
    groups = {}
    for nominee in nominees:
        group_key = nominee.acting_group
        nomination_id = nominee.nomination_id
        if group_key not in groups:
            groups[group_key] = set()
        groups[group_key].add(nomination_id)

    for group_key, nomination_ids in groups.items():
        if len(nomination_ids) > 1:
            raise HTTPException(400, detail='Нельзя голосовать за одного и того же актёра в связанных номинациях (acting_group)')

    # Existing database check for already-cast votes (omitted for brevity)
    # Exclude current nomination ids and compare by person_id
    #...

# In the _do_submit method:
try:
    # voting logic (omitted for brevity)
except HTTPException as e:
    return vote.html(context={'error_message': e.detail, 'errors': [e.detail]})

# Your earlier validation errors
if errors:
    return vote.html(context={'error_message': ' · '.join(errors), 'errors': errors})
