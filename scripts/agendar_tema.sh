#!/bin/zsh
# Uso: scripts/agendar_tema.sh "termo de busca"
# Espera a pausa do disjuntor da OAB/SP vencer (lê o estado pelo próprio servidor — tolera o Mac dormir) e roda UMA vez a
# busca dirigida por tema. Não insiste: se o portal recusar de novo, o disjuntor rearma e este script termina.
cd "$(dirname "$0")/.." || exit 1
TERMO="$1"; LOG="dados/tema-agendado.log"
mkdir -p dados
echo "[$(date '+%d/%m %H:%M:%S')] agendado: '$TERMO'" >> "$LOG"
while :; do
  FALTA=$(.venv/bin/python -c "import sys,time; sys.argv=['x']; import servidor_tedsp as t
e=t._ler_estado()['oabsp']; print(max(0,int(max(e['pausa_ate'],0)-time.time())))" 2>/dev/null || echo 600)
  [ "$FALTA" -le 0 ] && break
  sleep $(( FALTA < 60 ? FALTA + 5 : 60 ))
done
sleep 120   # folga depois do vencimento
echo "[$(date '+%d/%m %H:%M:%S')] pausa vencida — rodando" >> "$LOG"
TEDSP_TERMO="$TERMO" .venv/bin/python - >> "$LOG" 2>&1 <<'PY'
import os, sys, asyncio
sys.argv = ["x"]; sys.path.insert(0, ".")
import servidor_tedsp as t
try:
    r = asyncio.run(t._sincronizar_tema(os.environ["TEDSP_TERMO"], None, 200, print))
    print("RESULTADO:", {k: v for k, v in r.items() if k != "guids"})
    msg = f"{r['pareceres']} pareceres baixados ({r['encontradas']} ementas)" + (" — parou pelo orçamento" if r["parou_por_orcamento"] else "")
except Exception as ex:
    print("FALHOU:", ex); msg = "não concluiu: " + str(ex)[:120]
print(t._diagnostico())
if sys.platform == "darwin":
    os.system("osascript -e " + repr(f'display notification "{msg}" with title "TED-SP: {os.environ["TEDSP_TERMO"]}"'))
PY
echo "[$(date '+%d/%m %H:%M:%S')] fim" >> "$LOG"
