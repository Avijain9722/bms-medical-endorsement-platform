// Only the selected company's options are fetched, and only when it is chosen.
// Embedding the whole client master here cost 707 KB at 500 companies -- almost
// all of it for companies the operator was not going to pick.
function bmsFill(select, items, label) {
  select.innerHTML = '<option value="">(none)</option>';
  for (const item of items) {
    const option = document.createElement('option');
    option.value = item.id;
    option.textContent = label(item);
    select.appendChild(option);
  }
}
async function bmsClientChanged() {
  const id = document.getElementById('client_id').value;
  const subs = document.getElementById('sub_group_id');
  const entities = document.getElementById('legal_entity_id');
  const policies = document.getElementById('client_policy_id');
  const clear = () => {
    bmsFill(subs, [], () => ''); bmsFill(entities, [], () => ''); bmsFill(policies, [], () => '');
  };
  if (!id) { clear(); return; }
  try {
    const response = await fetch('/clients/' + encodeURIComponent(id) + '/options', {
      headers: {'Accept': 'application/json'}
    });
    if (!response.ok) { clear(); return; }
    const client = await response.json();
    bmsFill(subs, client.sub_groups, s => s.name + (s.emirate ? ' (' + s.emirate + ')' : ''));
    bmsFill(entities, client.entities, e => e.name);
    bmsFill(policies, client.policies,
            p => [p.insurer, p.policy_no, p.category].filter(Boolean).join(' \u00b7 '));
  } catch (error) {
    // Offline or the server refused: leave the dependent lists empty rather
    // than half-filled. The typed fields below still accept a manual entry.
    clear();
  }
}
document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('client_id').addEventListener('change', bmsClientChanged);
  bmsClientChanged();
});
