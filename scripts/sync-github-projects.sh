#!/bin/bash
# 同步 semantica-graph-server 任务到 GitHub Projects #5（用户级 Projects v2，PROJ-SEMANTICA-0001）。
# 用法: scripts/sync-github-projects.sh [items.tsv]
#   items.tsv 每行: 标题<TAB>状态<TAB>[优先级]；状态 ∈ 未开始|进行中|已完成，优先级 ∈ P0|P1|P2（可空）。
#   默认读取 docs/project-board.tsv（契约的一部分，随仓库提交）。
# 前置: Classic PAT（project scope）写入 /tmp/gh_token（chmod 600）。
# 幂等：按标题匹配，缺失创建，存在只更新字段。字段/选项 ID 每次自动发现。
set -uo pipefail
TSV="${1:-docs/project-board.tsv}"
TOKEN=$(cat /tmp/gh_token)
API=https://api.github.com/graphql
AUTH=(-H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json")
OWNER=shark8848
PROJ_NUMBER=5

gql() { # GitHub API 网络抖动时自动重试 5 次
  local body
  for _attempt in 1 2 3 4 5; do
    body=$(curl -sS --retry 2 --retry-all-errors --retry-delay 2 --max-time 45 "${AUTH[@]}" "$API" -d "$1") && [ -n "$body" ] && { printf '%s' "$body"; return 0; }
    sleep 2
  done
  return 1
}

# 一次性取回：项目 id、Status/Priority 字段与选项、已有条目（减少网络往返）。
DISCOVERY=$(gql '{"query":"query{user(login:\"'"$OWNER"'\"){projectV2(number:'"$PROJ_NUMBER"'){id title fields(first:50){nodes{... on ProjectV2SingleSelectField{name id options{id name}}}} items(first:100){nodes{id content{... on DraftIssue{title}}}}}}}"}') \
  || { echo "FATAL: discovery query failed (network or permission)" >&2; exit 1; }
PROJ=$(jq -r '.data.user.projectV2.id' <<<"$DISCOVERY")
[ -n "$PROJ" ] && [ "$PROJ" != "null" ] || { echo "FATAL: cannot resolve project #$PROJ_NUMBER" >&2; exit 1; }

F_STATUS=$(jq -r '.data.user.projectV2.fields.nodes[] | select(.name=="Status") | .id' <<<"$DISCOVERY" | head -1)
F_PRIO=$(jq -r '.data.user.projectV2.fields.nodes[] | select(.name=="Priority") | .id' <<<"$DISCOVERY" | head -1)

declare -A STATUS_OPT PRIO_OPT
while IFS=$'\t' read -r fname fid oname; do
  [ -z "$fname" ] && continue
  if [ "$fname" = "Status" ]; then STATUS_OPT[$oname]=$fid; else PRIO_OPT[$oname]=$fid; fi
done < <(jq -r '.data.user.projectV2.fields.nodes[] | select(.name=="Status" or .name=="Priority") | .name as $f | .options[] | [$f, (.id), .name] | @tsv' <<<"$DISCOVERY")

if [ -z "${STATUS_OPT[Backlog]:-}" ] && [ -z "${STATUS_OPT[未开始]:-}" ]; then
  echo "FATAL: Status options not resolved (network or permission)" >&2
  exit 1
fi

declare -A PR=([P0]="${PRIO_OPT[P0]}" [P1]="${PRIO_OPT[P1]}" [P2]="${PRIO_OPT[P2]}")

resolve_status() { # tsv_status -> option id；看板选项可能是中文或英文，按别名匹配
  case "$1" in
    未开始) for n in 未开始 Backlog Todo; do [ -n "${STATUS_OPT[$n]:-}" ] && { echo "${STATUS_OPT[$n]}"; return; }; done ;;
    进行中) for n in 进行中 "In progress" "In Progress" Doing; do [ -n "${STATUS_OPT[$n]:-}" ] && { echo "${STATUS_OPT[$n]}"; return; }; done ;;
    已完成) for n in 已完成 Done; do [ -n "${STATUS_OPT[$n]:-}" ] && { echo "${STATUS_OPT[$n]}"; return; }; done ;;
  esac
}

mapfile -t EXIST < <(jq -r '.data.user.projectV2.items.nodes[] | [.id, .content.title] | @tsv' <<<"$DISCOVERY")
declare -A IDS=()
for row in "${EXIST[@]}"; do IDS[${row#*$'\t'}]=${row%%$'\t'*}; done

set_field() { # item field option
  gql "$(jq -n --arg p "$PROJ" --arg i "$1" --arg f "$2" --arg o "$3" \
    '{query:"mutation($p:ID!,$i:ID!,$f:ID!,$o:String!){updateProjectV2ItemFieldValue(input:{projectId:$p,itemId:$i,fieldId:$f,value:{singleSelectOptionId:$o}}){projectV2Item{id}}}",variables:{p:$p,i:$i,f:$f,o:$o}}')" \
    | jq -r 'if .errors then "ERR " + (.errors[0].message) else "ok" end'
}
create_item() { # title -> item id
  gql "$(jq -n --arg p "$PROJ" --arg t "$1" \
    '{query:"mutation($p:ID!,$t:String!){addProjectV2DraftIssue(input:{projectId:$p,title:$t}){projectItem{id}}}",variables:{p:$p,t:$t}}')" \
    | jq -r 'if .errors then "ERR " + (.errors[0].message) else .data.addProjectV2DraftIssue.projectItem.id end'
}

ok=0; upd=0; fail=0
while IFS=$'\t' read -r title status prio; do
  [ -z "${title:-}" ] && continue
  [[ "$title" == \#* ]] && continue
  if [[ -n "${IDS[$title]:-}" ]]; then item=${IDS[$title]}; upd=$((upd+1)); else
    item=$(create_item "$title")
    if [[ "$item" == ERR* ]]; then echo "FAIL create: $title -> $item"; fail=$((fail+1)); continue; fi
  fi
  st_opt=$(resolve_status "$status")
  if [[ -z "$st_opt" ]]; then
    echo "FAIL status: $title -> no Status option matching '$status'"
    fail=$((fail+1)); continue
  fi
  r=$(set_field "$item" "$F_STATUS" "$st_opt")
  if [[ "$r" != ok ]]; then echo "FAIL status: $title -> $r"; fail=$((fail+1)); continue; fi
  if [[ -n "${prio:-}" && -n "${PR[$prio]:-}" && -n "$F_PRIO" ]]; then
    r=$(set_field "$item" "$F_PRIO" "${PR[$prio]}")
    [[ "$r" != ok ]] && echo "WARN prio: $title -> $r"
  fi
  ok=$((ok+1))
done < "$TSV"
echo "SYNC DONE ok=$ok updated=$upd fail=$fail"
