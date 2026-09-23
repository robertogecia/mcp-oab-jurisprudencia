#!/usr/bin/env python3
"""Anonimiza os fixtures antes de irem a um repositório público.

Os dados vêm de publicação oficial e aberta (DEOAB, ementário do TED-OAB/SP), mas nome e número de OAB de advogado
que é PARTE de processo disciplinar ou de inscrição não têm por que estar num repositório de código. Regras:
  · pessoa natural em `partes` → iniciais (é o que o próprio portal faz quando `apresentarNomeAbreviado`);
  · "Advogado(a): Fulano OAB/UF 00.000" e "(Advs: …)" no texto → iniciais, OAB zerada;
  · número de processo disciplinar do TED (sigiloso, art. 72 §2º EAOAB) → número sintético;
  · relator, revisor, presidente e conselheiro ficam: são função pública, e o parser precisa deles;
  · o campo `post` do modal (dump de ~450 KB do CMS, com caminhos internos do servidor) sai: o parser não o usa.
Roda sobre fixtures/ e é idempotente."""
import json, re, sys
from pathlib import Path

FX = Path(__file__).resolve().parent.parent / "fixtures"
INSTITUCIONAL = re.compile(r"(?i)conselho|ordem dos advogados|seccional|oab/\w+\s*$|minist[ée]rio|tribunal|uni[ãa]o|estado|munic[íi]pio")


def iniciais(nome: str) -> str:
    nome = re.sub(r"(?i)\s*OAB/\w+\s*[\d.\-A-Z]*\.?\s*$", "", nome).strip(" .")
    partes = [p for p in re.split(r"\s+", nome) if p and p[0].isalpha() and p.lower() not in ("de", "da", "do", "dos", "das", "e")]
    return ".".join(p[0].upper() for p in partes) + "." if partes else "X."


def main() -> None:
    # --- CFOAB: partes + advogados citados no texto
    for arq in ("cfoab_ementa.json",):
        p = FX / arq
        j = json.loads(p.read_text(encoding="utf-8"))
        trocas: dict[str, str] = {}
        for r in j.get("resultado") or []:
            for pt in r.get("partes") or []:
                nome = (pt.get("nome") or "").strip()
                if nome and not INSTITUCIONAL.search(nome) and not re.fullmatch(r"(?:[A-Z]\.){2,}", nome):
                    trocas[nome.strip(" .")] = iniciais(nome)
            for campo in ("descricaoEmenta", "descricaoAcordao"):
                t = r.get(campo) or ""
                for m in re.finditer(r"(?i)advogad[oa]s?:?\s+([A-ZÀ-Ü][A-Za-zÀ-ü']+(?:\s+[A-ZÀ-Ü][A-Za-zÀ-ü']+){1,6})\s*\(?\s*OAB/", t):
                    trocas[m.group(1).strip()] = iniciais(m.group(1))
                for m in re.finditer(r"\(Advs?\.?:\s*([^)]{5,400})\)", t):
                    for nome in re.findall(r"([A-ZÀ-Ü][A-Za-zÀ-ü' ]{4,60}?)\s+OAB/", m.group(1)):
                        trocas[nome.strip()] = iniciais(nome)
        texto = json.dumps(j, ensure_ascii=False)
        for nome in sorted(trocas, key=len, reverse=True):
            texto = re.sub(re.escape(nome), trocas[nome].replace("\\", "\\\\"), texto, flags=re.IGNORECASE)
        # número de inscrição de advogado-parte: zerado (mantém o formato, que o parser não usa)
        texto = re.sub(r"(OAB/[A-Z]{2})\s*(?:n[º°o.]\s*)?(\d{1,3}[.]?\d{3}(?:-?[A-Z])?)", lambda m: f"{m.group(1)} 00.000", texto)
        p.write_text(json.dumps(json.loads(texto), ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{arq}: {len(trocas)} nome(s) de pessoa natural → iniciais")

    # --- TED-SP: modal sem o dump do CMS
    for arq in ("modal_1994.json", "modal_2026.json"):
        p = FX / arq
        j = json.loads(p.read_text(encoding="utf-8"))
        antes = len(p.read_bytes())
        j.pop("post", None)
        p.write_text(json.dumps(j, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{arq}: {antes // 1024} KB → {len(p.read_bytes()) // 1024} KB (campo 'post' do CMS removido)")

    # --- TED-SP disciplinar: nº do processo disciplinar é sigiloso → sintético
    p = FX / "disciplinar_texto_5024.html"
    b = p.read_bytes().decode("utf-8", "replace")
    # por PADRÃO, nunca pelo número real: escrever aqui o número que se quer esconder o publicaria junto com o script
    b2 = re.sub(r"(?i)(processo disciplinar n\s*(?:<[^>]+>\s*)*o\s*(?:<[^>]+>\s*)*)(?!99R0000012011)[0-9A-Z]{10,16}",
                r"\g<1>99R0000012011", b)
    p.write_bytes(b2.encode("utf-8"))
    print(f"disciplinar_texto_5024.html: nº do processo disciplinar → sintético ({'trocado' if b2 != b else 'já estava'})")


if __name__ == "__main__":
    main()
