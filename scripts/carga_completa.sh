#!/bin/zsh
# Carga ÚNICA do ementário deontológico para montar o pacote do release (uso do mantenedor, não do usuário final).
# Ritmo do disjuntor (espaçamento, escada, pausa); retoma sozinho quando a pausa vence; mantém o Mac acordado enquanto roda.
# No fim: exporta o pacote, confere e — só se TODAS as verificações passarem — anexa ao release. Se uma falhar, não publica.
cd "$(dirname "$0")/.." || exit 1
LOG="dados/carga-completa.log"; mkdir -p dados
caffeinate -i -w $$ &
log() { echo "[$(date '+%d/%m %H:%M:%S')] $*" >> "$LOG"; }
avisar() { [ "$(uname)" = "Darwin" ] && osascript -e "display notification \"$1\" with title \"Pacote TED-SP\"" 2>/dev/null; }
log "início da carga"
for i in $(seq 1 40); do
  .venv/bin/python servidor_tedsp.py --sincronizar --sem-disciplinar >> "$LOG" 2>&1
  if [ $? -eq 0 ]; then log "carga completa na rodada $i"; break; fi
  FALTA=$(.venv/bin/python -c "import sys,time; sys.argv=['x']; import servidor_tedsp as t
e=t._ler_estado()['oabsp']; print(max(0,int(e['pausa_ate']-time.time())))" 2>/dev/null || echo 600)
  log "rodada $i interrompida; retomo em $((FALTA + 120)) s"
  sleep $((FALTA + 120))
done
# ── exportar e conferir
rm -rf pacote && .venv/bin/python servidor_tedsp.py --exportar-pacote pacote >> "$LOG" 2>&1 || { log "EXPORTAÇÃO FALHOU"; avisar "exportação falhou — veja $LOG"; exit 1; }
RES=$(.venv/bin/python - <<'PY'
import gzip, hashlib, json, re, sys
p = "pacote/base-tedsp.jsonl.gz"
b = open(p, "rb").read()
esp = open("pacote/SHA256SUMS.txt").read().split()[0]
t = gzip.decompress(b).decode("utf-8")
linhas = [json.loads(l) for l in t.splitlines() if l.strip()]
dados = [l for l in linhas if "_pacote" not in l]
com_texto = sum(1 for l in dados if l.get("texto"))
falhas = []
if hashlib.sha256(b).hexdigest() != esp: falhas.append("hash não confere")
if com_texto < 3000: falhas.append(f"só {com_texto} pareceres com texto (esperado ≥ 3000)")
if sum(1 for l in dados if not l.get("texto")) > 0.02 * len(dados): falhas.append("mais de 2% só com título: carga incompleta")
if re.search(r'"tema:', t) or '"meta"' in t: falhas.append("histórico de temas vazou para o pacote")
if any(set(l) - {"guid","slug","numero","titulo","ano_lista","html","texto"} for l in dados): falhas.append("campo inesperado no pacote")
print(("OK " if not falhas else "FALHA ") + f"{com_texto} com texto de {len(dados)} · " + "; ".join(falhas))
PY
)
log "conferência: $RES"
case "$RES" in
  OK*) gh release upload v1.4.0 pacote/base-tedsp.jsonl.gz pacote/SHA256SUMS.txt --repo robertogecia/mcp-oab-jurisprudencia --clobber >> "$LOG" 2>&1 \
         && { log "PACOTE PUBLICADO no release v1.4.0"; avisar "pacote publicado: $RES"; } \
         || { log "UPLOAD FALHOU"; avisar "upload falhou — veja $LOG"; } ;;
  *) avisar "NÃO publicado: $RES"; log "NÃO publicado" ;;
esac
