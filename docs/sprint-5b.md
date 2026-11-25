# Sprint 5B — Kimodo : état de la qualification avant l’inférence

**Critère de livraison non atteint.** Sur `sprint-5b-kimodo`, base `21e086065909e8e802276fe93c5f88cdf6a4a0dc`, aucune inférence Kimodo n’a démarré et aucune Action Rigify issue de Kimodo n’a été produite. La version reste **0.5.0**. Seule l’archive expérimentale historique 5A a été reconstruite ; il n’y a pas de build Kimodo ni de repli Linear/Smooth.

## Reprise — téléchargement à effectuer par l’utilisateur

L’utilisateur signale avoir préparé l’accès Llama. **L’autorisation n’a pas encore été vérifiée sous son identité** : le HTTP 401 ci-après est le résultat historique anonyme. Ni l’assistant ni les workers n’utilisent les identifiants. Les commandes suivantes sont destinées exclusivement à l’utilisateur, dans un **nouveau Bash interactif**, sans capture de sa session. Saisir un jeton de lecture à portée minimale dans le prompt masqué, jamais sur la ligne de commande ou dans le chat. Les conditions Meta (licence et AUP) et NVIDIA doivent être acceptées/respectées avant téléchargement ; voir les liens du [plan](sprint-5b-plan.md#4--périmètre-et-droits).

La liste minimale est déduite des chargeurs et des arbres distants au pin, pas d’un chargement réussi. **21 fichiers, 17 538 564 301 octets (16,33 Gio)** : Kimodo 1 133 185 135, MNTP 176 967 840, MNTP supervisé 167 830 346, Llama 16 060 580 980 octets. Pas de poids PyTorch alternatifs, dossier `original/`, tokenizer Llama en double ni code distant à exécuter. `/var/tmp` est sur Btrfs persistant sur la machine testée (202 Gio libres au relevé) ; utiliser un répertoire unique et privé, hors Git, et ne pas y placer de fichiers personnels. Ce volume peut faire l’objet d’un nettoyage périodique ; si nécessaire, choisir avant le dry-run un autre volume privé persistant équivalent. Les 20 Gio du contrôle sont une marge **pour les modèles**, pas pour les modèles plus l’environnement.

**Étape 1 — dry-run uniquement.** Dans `bash --noprofile --norc`, `uv` installe `huggingface_hub==1.4.1` dans un venv privé, sans jeton. Le dry-run combine l’API `hf_hub_download(dry_run=True)` et un contrôle des métadonnées de chaque fichier au pin. Aucun poids n’est lu à cette étape.

```bash
set +xa
set -euo pipefail
umask 077
test -d /var/tmp
RUN=$(mktemp -d /var/tmp/sas-kimodo-models.XXXXXXXX)
printf 'Répertoire des snapshots : %s\n' "$RUN"
mkdir -m 700 "$RUN/hub" "$RUN/home"
H="$RUN/hub"
df -PT -- "$H"
avail=$(df -B1 --output=avail -- "$H" | tr -dc '0-9')
test "$(stat -f -c %T -- "$H")" = btrfs
test "$avail" -ge $((20 * 1024 * 1024 * 1024))

uv_clean() {
  env -i PATH=/usr/bin:/bin HOME="$RUN/home" UV_CACHE_DIR="$RUN/uv" \
    UV_PYTHON_DOWNLOADS=never uv --no-config "$@"
}
uv_clean venv --python /usr/bin/python "$RUN/venv"
uv_clean pip install --python "$RUN/venv/bin/python" --only-binary :all: 'huggingface_hub==1.4.1'

unset HF_RUN_TOKEN
trap 'unset HF_RUN_TOKEN' EXIT
cat > "$RUN/models.py" <<'PY'
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

from huggingface_hub import get_hf_file_metadata, hf_hub_download, hf_hub_url

run, mode = Path(sys.argv[1]).resolve(strict=True), sys.argv[2]
cache = run / "hub"
proof = run / "dry-run.json"
token = sys.stdin.readline().strip()
if not token or mode not in {"dry", "get"}:
    raise SystemExit("Jeton ou mode absent/invalide.")

llama = "meta-llama/Meta-Llama-3-8B-Instruct"
mntp = "McGill-NLP/LLM2Vec-Meta-Llama-3-8B-Instruct-mntp"
repos = (
    (llama, "8afb486c1db24fe5011ec46dfbe5b5dccdb575c2", {
        "config.json": 654, "model.safetensors.index.json": 23950,
        **{f"model-{i:05}-of-00004.safetensors": n for i, n in enumerate(
            (4976698672, 4999802720, 4915916176, 1168138808), 1)},
    }),
    ("nvidia/Kimodo-SOMA-RP-v1.2", "0e3c89024d896064b6851201373f7834784a386b", {
        "config.yaml": 675, "model.safetensors": 1133177724,
        **{f"stats/motion/{part}/{stat}.npy": n for part, n in
           (("body", 3040), ("global_root", 168), ("local_root", 160))
           for stat in ("mean", "std")},
    }),
    (mntp, "31474e395ada192e8ed1586db6be79fb3b70c9c0", {
        "adapter_config.json": 794, "adapter_model.safetensors": 167829552,
        "config.json": 781, "tokenizer.json": 9085671, "tokenizer_config.json": 51042,
    }),
    (mntp + "-supervised", "baa8ebf04a1c2500e61288e7dad65e8ae42601a7", {
        "adapter_config.json": 794, "adapter_model.safetensors": 167829552,
    }),
)

def require(ok, message):
    if not ok:
        raise SystemExit(message)

def request(fn, repo, name, **kwargs):
    try:
        return fn(**kwargs)
    except Exception as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        # Ne jamais afficher les URL signées, en-têtes ou détails d'exception.
        raise SystemExit(f"Échec {repo}/{name}: {type(exc).__name__}, HTTP {status}") from None

def options(repo, rev, name):
    return dict(repo_id=repo, filename=name, revision=rev, cache_dir=cache,
                token=token if repo == llama else False)

if mode == "dry":
    require(not any(cache.iterdir()) and not proof.exists(), "Dry-run : cache neuf requis.")
manifest = {}
for repo, rev, files in repos:
    for name, size in files.items():
        meta = request(get_hf_file_metadata, repo, name,
                       url=hf_hub_url(repo, name, revision=rev),
                       token=token if repo == llama else False)
        etag = meta.etag or ""
        pattern = r"[a-f0-9]{64}" if name.endswith((".safetensors", ".npy")) else r"[a-f0-9]{40}"
        require(meta.commit_hash == rev and meta.size == size and re.fullmatch(pattern, etag),
                f"Métadonnées absentes ou inattendues : {repo}/{name}")
        if repo == "nvidia/Kimodo-SOMA-RP-v1.2" and name == "model.safetensors":
            require(etag == "fdcef624140a12c0a4840192ce0b75cafb4e73228a893da3864b4a990d3e919e",
                    "Le checkpoint Kimodo ne correspond pas à l’empreinte du plan.")
        if mode == "dry":
            dry = request(hf_hub_download, repo, name, dry_run=True, **options(repo, rev, name))
            require(dry.commit_hash == rev and dry.filename == name and dry.file_size == size
                    and not dry.is_cached and dry.will_download, f"Dry-run invalide : {repo}/{name}")
        manifest[f"{repo}/{name}"] = dict(revision=rev, size=size, etag=etag)
    print(repo, rev, len(files), sum(files.values()), "octets", flush=True)

record = json.dumps({"cache": str(cache), "files": manifest}, indent=2) + "\n"
if mode == "dry":
    proof.write_text(record, encoding="utf-8")
    raise SystemExit(0)

require(proof.is_file() and proof.read_text(encoding="utf-8") == record,
        "Dry-run absent, au mauvais emplacement, ou métadonnées modifiées.")
require(shutil.disk_usage(cache).free >= 20 * 1024**3, "Moins de 20 Gio avant téléchargement.")
for repo, rev, files in repos:
    root = cache / ("models--" + repo.replace("/", "--"))
    snapshot = root / "snapshots" / rev
    for name, size in files.items():
        p = Path(request(hf_hub_download, repo, name, **options(repo, rev, name)))
        etag = manifest[f"{repo}/{name}"]["etag"]
        require(p == snapshot / name and p.is_symlink() and p.resolve(strict=True) == root / "blobs" / etag
                and p.stat().st_size == size, f"Chemin ou taille invalide : {repo}/{name}")
        h = hashlib.sha256() if len(etag) == 64 else hashlib.sha1(f"blob {size}\0".encode())
        with p.open("rb") as f:
            for chunk in iter(lambda: f.read(4 * 1024**2), b""):
                h.update(chunk)
        require(h.hexdigest() == etag, f"Empreinte incorrecte : {repo}/{name}")
    require({str(p.relative_to(snapshot)) for p in snapshot.rglob("*") if p.is_file() or p.is_symlink()}
            == set(files), f"Liste locale différente : {repo}")
    if repo == llama:
        index = json.loads((snapshot / "model.safetensors.index.json").read_text(encoding="utf-8"))
        require(set(index["weight_map"].values()) == {n for n in files if n.endswith(".safetensors")},
                "Index Llama différent des shards attendus.")
    print("Vérifié :", repo, flush=True)
(run / "verified.json").write_text(record, encoding="utf-8")
PY

models() {
  env -i PATH=/usr/bin:/bin HOME="$RUN/home" HF_HOME="$RUN/home/hf" HF_HUB_CACHE="$H" \
    HF_ENDPOINT=https://huggingface.co HF_HUB_DISABLE_TELEMETRY=1 HF_HUB_DISABLE_XET=1 \
    "$RUN/venv/bin/python" -I -B "$RUN/models.py" "$RUN" "$1" <<< "$HF_RUN_TOKEN"
}
IFS= read -rs -p 'Jeton HF de lecture (saisie masquée) : ' HF_RUN_TOKEN
printf '\n'
models dry
unset HF_RUN_TOKEN
```

Vérifier les quatre listes dans l’ordre **6 / 8 / 5 / 2** et le total annoncé. Au moindre `401`/`403`, fichier absent ou taille inattendue : **ne pas télécharger**. Le cache est unique, pas de `--local-dir` ni de deuxième copie des poids.

**Étape 2 — téléchargement et vérification, seulement après un dry-run conforme, dans le même Bash.** Le script exige le manifeste du dry-run pour ce cache, revalide les métadonnées avant de télécharger, puis recalcule SHA-256 pour les blobs LFS et l’OID Git pour les petits fichiers. L’ETag vient de la résolution HF officielle au pin, jamais d’un fichier local. Le Hub ne reçoit le jeton que pour Llama ; le script le lit sur stdin, ne le transmet pas dans l’environnement et n’enregistre ni jeton ni URL signée.

```bash
IFS= read -rs -p 'Jeton HF de lecture (saisie masquée) : ' HF_RUN_TOKEN
printf '\n'
models get
unset HF_RUN_TOKEN
printf 'Cache : %s\nManifeste vérifié : %s\n' "$H" "$RUN/verified.json"
```

Transmettre uniquement les **chemins du cache et de `verified.json`** et la confirmation que les quatre vérifications sont réussies, jamais le jeton ni un log brut. Avant l’inférence, revalider hors ligne les liens, tailles, empreintes et pins d’après le manifeste et monter **seulement** le cache vérifié en lecture seule dans le worker, pas le HOME de téléchargement. Le SHA-256 de Kimodo est `fdcef624140a12c0a4840192ce0b75cafb4e73228a893da3864b4a990d3e919e`. Pour la résolution hors ligne, créer des vues par liens vers les pins Kimodo et McGill sous `CHECKPOINT_DIR` et `TEXT_ENCODERS_DIR`. Le loader Transformers suit l’ID Llama de `adapter_config.json` sans révision explicite : préparer dans une vue privée du cache le `refs/main` local, avec le **SHA Llama du plan** (sans saut de ligne), et vérifier que la résolution de chaque fichier par `main` et par SHA donne le même snapshot. Ce n’est pas une résolution de la branche distante. Ne pas modifier le contenu des snapshots épinglés.

## Reprise — résultats observés sans poids

- **ROCm : premier kernel en échec.** Sur CachyOS, CPython 3.14.10 et roue officielle PyTorch `2.9.1+rocm6.4` (SHA-256 `5cd5daeaa956e92823a9feccbcfebcf257106a71e068cc951ffc29fb44481afe`), HIP `6.4.43484-123eb5128` chargé depuis la roue, CUDA `None`. Dans un venv `bwrap --unshare-net` sans HOME utilisateur ni contournement d’architecture, `torch.cuda.is_available()` est vrai et `gcnArchName` vaut `gfx1032`. Allocation FP32 réussie ; aller-retour CPU↔GPU synchronisé et exact pour `[1.0, -2.0, 3.5, 0.25]`. Puis **`x + 1.0` lève `HIP error: invalid device function`**, sortie 1. `torch.cuda.get_arch_list()` contient `gfx1030`, pas `gfx1032`. GEMM FP32/FP16/BF16, attention et normalisation n’ont pas été exécutées après cette erreur. Cela tranche pour la **roue testée**, pas pour toutes les distributions ROCm.
- La sonde HIP a duré **11,999 s** dont 2,140 s d’import, RSS maximale **2 784 224 Kio** (~2,66 Gio), pics allocateur Torch **1 024 octets alloués / 2 097 152 octets réservés**. La VRAM globale sysfs n’a été relevée qu’avant/après (1 489 281 024 → 1 657 843 712 octets), pas de pic global mesuré. Aucune mesure Kimodo. La roue `2.15.0+rocm10.0` n’a pas été testée : roue et composants ROCm officiels représentent au moins 8,76 Go de téléchargements avant Triton et les autres dépendances selon leurs métadonnées. Aucun changement de pilote ou de cible GPU.
- **CPU : import Kimodo au pin réussi**, sans checkpoint ni encodeur. CPython **3.12.16**, Torch **`2.11.0+cpu`**, Transformers **5.1.0** ; 79 roues vérifiées par SHA-256 pour l’installation. Wheel Kimodo construite avec `SKIP_MOTION_CORRECTION_IN_SETUP=1` et `pip wheel --no-index --no-deps --no-build-isolation`, puis `pip install --no-index --only-binary=:all: --require-hashes`, `pip check`, et `python -I -B -c 'import kimodo'` dans un sandbox hors réseau. Au premier import, Torch échouait sur l’absence de compte système visible (`getpwuid`) ; la relance avec cache `TORCHINDUCTOR_CACHE_DIR` privé et `LOGNAME` synthétique a réussi. Construction/installation **40,3 s, pic cgroup 1,9 Gio** ; import réussi **4,11 s, pic cgroup 283,6 Mio**, swap du job 0 dans les deux cas. Les sessions jetables ont été nettoyées. Il faudra reconstruire le venv pour un job avec poids.
- **Garde-fou mémoire vérifié** : cgroup v2 d’une unité utilisateur transitoire, `MemorySwapMax=0`, `memory.oom.group=1`. Une allocation de 96 Mio sous plafond 64 Mio a déclenché le `oom-kill` attendu. Au relevé : **16 869 740 Kio** de RAM disponible, **24 482 812 Kio** de swap dont 2 496 Kio utilisés. Un seuil RSS seul ne remplace pas le plafond cgroup. Ne fixer le plafond du job qu’après mesure de la RAM disponible, en gardant une réserve pour l’hôte ; ne pas utiliser le swap pour forcer le chargement.

### Prochaine mesure : encodeur et diffusion en deux processus

Après réception du cache vérifié, lancer **LLM2Vec CPU seul** via `LLM2VecEncoder` avec le preset BF16 et `TEXT_ENCODER_DEVICE=cpu`, dans un cgroup sans swap et avec un délai de diagnostic (300 s). Relever séparément le chargement, l’encodage, la RSS, `memory.peak`/`memory.events` et le périphérique. Le corps Llama sans tête LM représente environ **14 Gio** de paramètres BF16 d’après les dimensions, avant les adaptateurs et le runtime ; le merge MNTP utilise un temporaire dense par projection (jusqu’à 112 Mio BF16 ou 224 Mio FP32 par tenseur), pas une seconde instance intégrale. Ces estimations ne sont pas des observations et l’essai n’est pas admis d’office à ~16 Gio disponibles. Ne pas poursuivre si le seuil avec réserve ne le permet pas, ou si OOM, chargement incomplet ou erreur d’attention. Contrôler la bidirectionnalité effective : le portage redéfinit un hook de masque que le `forward` Transformers 5.1.0 n’appelle pas directement ; forcer `eager` n’est pas un repli équivalent.

Si l’encodeur réussit, générer et conserver l’embedding **réel** du prompt aseptisé avec un manifeste (pins, empreintes de la pile et des poids, prompt exact, dtype, backend d’attention, dimensions, SHA-256). Attendre la fin complète du processus pour libérer la RAM. Le second processus charge seulement le diffuseur sur CPU, sans accès aux poids texte, via le point d’injection amont `load_model(..., text_encoder=...)` et un callable qui refuse tout écart de prompt. Ne pas confondre ce rejeu avec une validation du CLI complet. Le serveur Gradio officiel reste autohébergé : il consomme la même RAM texte et ne réduit pas le pic si le diffuseur tourne en même temps. La quantification n’a pas été testée ; elle demanderait une qualification de LLM2Vec et une comparaison au BF16, pas un remplacement arbitraire.

**Statut de la reprise :** nouveau blocage GPU reproduit ; côté CPU, accès authentifié et snapshots encore à confirmer par l’utilisateur, chargements non mesurés. Aucun embedding, NPZ ou résidu de pose calculé par le modèle. Pas d’intégration Blender à ce stade. Les validations historiques ci-dessous n’ont pas été relancées.

## Historique — pins, accès et licences

Le [plan 5B](sprint-5b-plan.md) reste la référence pour le [code Kimodo `e1c219c99b4b94ba48be1fb6a1cbc64ecee4c954`](https://github.com/nv-tlabs/kimodo/tree/e1c219c99b4b94ba48be1fb6a1cbc64ecee4c954), Apache-2.0 avec notice MIT pour le portage LLM2Vec, et le [checkpoint `Kimodo-SOMA-RP-v1.2@0e3c89024d896064b6851201373f7834784a386b`](https://huggingface.co/nvidia/Kimodo-SOMA-RP-v1.2/tree/0e3c89024d896064b6851201373f7834784a386b), sous NVIDIA Open Model License. Le checkpoint smooth-root attend SOMA 30 à 30 i/s ; le fichier principal annoncé fait 1 133 177 724 octets, hors statistiques requises. Aucun poids n’a été téléchargé ni contrôlé localement.

Les adaptateurs McGill MIT épinglés (`31474e395ada192e8ed1586db6be79fb3b70c9c0` et `baa8ebf04a1c2500e61288e7dad65e8ae42601a7`) référencent tous deux `meta-llama/Meta-Llama-3-8B-Instruct`, soumis à la Meta Llama 3 Community License et à l’Acceptable Use Policy. La sonde anonyme du pin `8afb486c1db24fe5011ec46dfbe5b5dccdb575c2`, sous environnement nettoyé et `HOME` vide, a obtenu **HTTP 401 / `GatedRepo`** pour `HEAD` et pour `GET` borné. Le code retour `curl` était 0 : transport réussi, accès refusé. Aucun identifiant n’a été recherché ni lu. Aucun snapshot autorisé complet n’a été fourni à la session.

Sonde HTTP exécutée pendant la qualification 5B (extrait sous environnement nettoyé et `HOME` vide) :

```bash
u=https://huggingface.co/meta-llama/Meta-Llama-3-8B-Instruct/resolve/8afb486c1db24fe5011ec46dfbe5b5dccdb575c2/config.json
curl -q --proto =https --connect-timeout 5 --max-time 15 --max-filesize 8192 -sS -I -D - -o /dev/null "$u"
curl -q --proto =https --connect-timeout 5 --max-time 15 --max-filesize 8192 -sS --range 0-1023 -D - -o /dev/null "$u"
```

L’accès à cette base est bloquant **pour les deux périphériques** avec l’encodeur local prévu. Il ne doit pas être contourné avec un modèle, un encodeur distant ou des embeddings de substitution. Aucun code tiers, poids, corpus, exemple ni résultat amont n’est redistribué. Les droits sur d’éventuelles sorties et sur leurs entrées seront à vérifier avant tout export public, selon le plan.

## Machine, isolation et sondes observées

- CachyOS x86-64, noyau `7.3.5-2-cachyos`, glibc `2.44`, Ryzen 5 3600 (AVX2), Python système `3.14.10`. RAM : 23,35 Gio au total, ~16 Gio disponibles pendant la qualification. Environ 202 Gio libres sur le volume contenant le dépôt ; la racine de test `/tmp/opencode` est sur un **tmpfs** d’environ 12 Gio, pas sur ce volume.
- RX 6600 (`1002:73ff`, `gfx1032`), pilote actif `amdgpu`, `/dev/kfd` et `/dev/dri/renderD128` présents. `rocm-core` et `hip-runtime-amd` `10.1-2.1` ; `hsa-rocr` et `rocminfo` `10.1-1.1`. VRAM physique ~7,98 Gio, ~1,18 Gio utilisée au relevé avant inférence ; **ce n’est pas un pic**.
- Les sondes isolées ont utilisé `unshare --user --map-root-user --net` dans une session privée avec `HOME` vide. `ip -br link` a montré seulement `lo` à l’arrêt ; `ip -4 route show table all` et `ip -6 route show table all` n’ont renvoyé aucune route. Dans le même espace réseau, `/opt/rocm/bin/rocminfo` a détecté `gfx1032` (HSA 1.21, extension 1.32). L’essai complet de ce contrôle a pris 0,064 s. C’est une preuve d’énumération ROCm, pas une exécution de tenseurs HIP. Ce contrôle réseau n’isole pas le système de fichiers.

| Parcours | Mesuré | Arrêt ou limitation |
|---|---|---|
| PyTorch HIP (qualification initiale) | L’[index officiel ROCm 10.0](https://download.pytorch.org/whl/rocm10.0/torch/) annonce `torch-2.15.0+rocm10.0` pour `cp314`, x86-64. | Ni la [matrice 10.0](https://rocm.docs.amd.com/en/docs-10.0.0/compatibility/compatibility-matrix.html) ni la [matrice 10.1](https://rocm.docs.amd.com/en/docs-10.1.0/compatibility/compatibility-matrix.html) ne listent `gfx1032` ou CachyOS. Cette roue n’a pas été téléchargée ni testée. Voir plus haut l’échec distinct de la roue `2.9.1+rocm6.4` à la reprise. |
| PyTorch CPU | Dans un venv jetable, Torch **`2.14.1+cpu`**, HIP `None`, CUDA `None`, `torch.cuda.is_available() == False`. Deux produits matriciels 64×64 FP32/BF16, six threads, résultats exacts ; environ 0,41 ms et 0,17 ms sur une exécution, non représentative d’un benchmark. | `pip check` réussi pour la pile Torch seule. Aucune mesure de chargement Kimodo, de LLM2Vec ou de diffusion. |
| Kimodo CPU | Aucun lancement. | HTTP 401 sur Llama ; le tmpfs ne peut pas stocker à lui seul les poids Llama non quantifiés (~16 Go en BF16), avant Kimodo, les adaptateurs et le venv. La RAM disponible ne laisse pas de marge démontrée pour le chargement et la fusion des adaptateurs. Pas de bascule sur swap ou de quantification improvisée. |

Pour la sonde CPU, le venv a été créé avec `/usr/bin/python -I -B -m venv --without-pip "$RUN/venv"`. Onze roues officielles (208 531 261 octets) ont été vérifiées par taille et SHA-256 ; SHA-256 de la roue Torch : `35103e180214793207f95d7e12e100ecba3ace6f5fb37e1fcefbb07e096ed180`. Après amorçage de `pip` depuis sa roue vérifiée, installation hors réseau avec `pip install --no-cache-dir --no-compile --no-index --only-binary=:all: --find-links wheels --require-hashes -r requirements.lock`, puis `"$RUN/venv/bin/python" -I -B -m pip --disable-pip-version-check check`. La sonde, exécutée par `"$RUN/venv/bin/python" -I -B smoke.py`, a relevé **3,903 s** écoulées (import : **2,03 s**) et **280 604 Kio** de RSS maximale du processus (~274 Mio). Pas de VRAM utilisée par ce calcul CPU. Venv, roues et scripts de session supprimés après contrôle. Ce verrouillage ne couvre pas les dépendances Kimodo : Transformers `5.1.0` et PEFT `0.18.1` ont été examinés sur métadonnées, non installés ; `antlr4-python3-runtime==4.9.3` demandait un build depuis la source sur l’index examiné.

Au pin Kimodo, `TEXT_ENCODER_MODE=local` évite la sonde de service HTTP ; `TEXT_ENCODER_DEVICE=cpu` est lu après `from_pretrained` et déplace l’encodeur sur CPU avant l’encodage. Il ne règle pas la résolution initiale du snapshot ni le périphérique du diffuseur. Le défaut BF16 et ce parcours ont été **lus dans le code**, pas exercés avec le checkpoint ni l’encodeur. Pour réévaluer la voie CPU, viser un volume privé hors tmpfs et une marge de départ d’environ **24 Gio de RAM disponible** (estimation prudente, pas un minimum démontré), avec une enveloppe de diagnostic de 300 s et arrêt de l’arbre du job au dépassement. Ne pas extrapoler la durée d’une inférence à partir de la petite sonde Torch.

## Première preuve à reprendre quand les prérequis seront levés

Entrée proposée, **non fournie au modèle** : un pas du pied gauche, 36 images à 30 i/s, deux poses `fullbody` aux indices 0 et 30 et une cible `left-foot` à 15. La vérification indépendante de la cinématique directe au pin confirme un avant `+Z`, avec Y vertical en mètres et rotations axe-angle locales en radians. Pour les cibles calculées, la base d’orteil gauche avance de 26,5 cm et décolle de 4,6 cm à l’image 15 ; ces valeurs ne sont **pas** des prédictions ni des mesures de contact. Une contrainte `fullbody` conditionne des positions calculées par FK, pas un verrouillage de chaque rotation. L’appui droit n’est pas imposé entre les deux poses complètes.

Recette originale du JSON à produire hors dépôt (`step.json`) : initialiser chaque pose à 30 vecteurs axe-angle nuls ; donner −1,35 rad autour de Z à `LeftArm` (11) et +1,35 à `RightArm` (17). Pour chaque jambe, écrire les angles X de hanche, genou et cheville sous la forme `(a, b, −a−b)` aux indices 22–24 à gauche et 26–28 à droite.

| Image / type | Racine XYZ (m) | `(a,b)` gauche | `(a,b)` droite |
|---|---|---|---|
| 0 / `fullbody` | `(0, 0.988544695, 0)` | `(0, 0)` | `(0, 0)` |
| 15 / `left-foot` | `(0, 0.970415455, 0.117342739)` | `(-0.36, 0.72)` | `(0.07, 0.14)` |
| 30 / `fullbody` | `(0, 0.939048644, 0.232125794)` | `(-0.35, 0.631134165)` | `(0.20, 0.16)` |

Regrouper les lignes du même type dans un objet contenant `type`, `frame_indices`, `root_positions` et `local_joints_rot`, dans un tableau JSON. `smooth_root_2d` peut être omis pour utiliser le XZ du bassin. Commande prévue, **non exécutée**, à lancer uniquement dans l’espace réseau vérifié et après installation et validation des pins d’après le plan :

```bash
HF_HOME="$RUN/hf" HF_HUB_CACHE="$RUN/model-cache" HUGGINGFACE_CACHE_DIR="$RUN/model-cache" \
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_IMPLICIT_TOKEN=1 \
CHECKPOINT_DIR="$RUN/checkpoints" TEXT_ENCODERS_DIR="$RUN/encoders" \
TEXT_ENCODER_MODE=local TEXT_ENCODER_DEVICE=cpu \
"$RUN/venv/bin/kimodo_gen" "A person takes one step forward with the left foot." \
  --model Kimodo-SOMA-RP-v1.2 --duration 1.2 --num_samples 1 \
  --diffusion_steps 20 --seed 42 --no-postprocess \
  --constraints "$RUN/step.json" --output "$RUN/step_raw"
```

Le profil brut suppose un build avec `SKIP_MOTION_CORRECTION_IN_SETUP=1`, conformément au plan. `$RUN/model-cache` désigne la vue montée en lecture seule des snapshots vérifiés, avec la référence Llama locale décrite plus haut. Avant d’exécuter, obtenir l’accès Llama autorisé sans transmettre de jeton au worker, vérifier la résolution transitive **hors ligne** et les empreintes de tous les fichiers. Le CLI choisit son périphérique : contrôler le périphérique effectif et rejeter une bascule non prévue. Au premier résultat, mesurer temps, RSS et VRAM, borner et lire `step_raw.npz` sans pickle, vérifier valeurs finies, formes et FK (77 joints en sortie, dont 30 prédits). Mesurer les résidus aux poses 0/30 et aux cibles de cheville/base d’orteil gauches (indices 69/70 en sortie) à l’image 15. Comparer avec une génération sans contraintes à pile et graine identiques, puis séparer la correction amont des corrections SAS. **Actuellement : aucun NPZ, temps d’inférence, pic d’inférence, clip ou comparatif visuel.**

## Non-régression et décision de livraison

Commandes exécutées pour la non-régression du ZIP historique 5A, sans Kimodo :

```bash
bash scripts/build_extension.sh
SAS_MOTION_TEST_PYTHON=/usr/bin/python3 SOULS_TEST_GODOT=1 bash scripts/test_headless.sh
bash tests/test_shell_failures.sh dist/souls_animation_studio-0.5.0-dev-sprint-5a.zip
```

Sur la base intacte : **113 réussites, 0 échec, 7 skips attendus** selon le partage des tests entre CPython et Blender. Blender **5.2.1 LTS** (`9e2066aef7ef`, Python **3.13.13**), Rigify **0.6.10**, CPython **3.14.10** avec NumPy **2.5.5**, Godot **4.7.2**. Installation hors ligne, réactivation et désinstallation testées ; assertion Blender et ZIP corrompu remontent le code 1 attendu. Hors décompte : retarget planaire et preuve MLP historiques (22 échantillons chacun), aller-retour GLB et trois imports dans des projets Godot temporaires réussis. La revue indépendante a rejoué la recette sur le ZIP et retrouvé le même bilan. Rien de cela ne valide Kimodo, la recette en fenêtre ni le rendu visuel. ZIP 5A reconstruit pendant la qualification 5B : 42 entrées, 121 574 octets, SHA-256 `bb5b5b084f800cee659bf3f88e77ead4c886ed90862a26ac865be524c429046c` (distinct de celui de l’archive corrigée du rapport 5A).

Pas de mapping SOMA↔Rigify, d’intégration UI, de correction/contact Kimodo ni de mesure sur les deux morphologies faute de sortie réelle. Les références Linear/Smooth du [banc 5A](sprint-5a.md#banc-canonique-original) restent des baselines, pas un comparatif avec Kimodo. Le [contrat v1](../backends/inbetweening/contract-v1.md), le code de l’extension et le projet Godot principal restent inchangés. Pas de démonstration ni d’export Kimodo, de publication 0.6.0 ou de push. Le point de reprise est un premier job réellement contraint avec les pins du plan et un stockage autorisé adapté ; la phase Rigify n’ouvre qu’après mesure de cette sortie.
