# Sprint 4 — preuve CPU et décision de branchement

**Décision : un seul moteur expérimental, le MLP original `sprint4_worker.py`, accessible dans Blender pour son profil planaire.** Entraînement et inférence tournent sur **CPU avec NumPy**, sans HIP, ROCm ni PyTorch. Le modèle ne bat pas la référence CPU sur ce banc et ne satisfait pas le test de prise mobile. Il ne remplace donc pas Linear/Smooth. Le [contrat v1](../backends/inbetweening/contract-v1.md) reste inchangé.

## 1. Candidats et droits indépendants

**Lien UniMate demandé :** <https://github.com/LUXHIB/UniMate-Open-Source-AI-Animator>. Vérifié le 25 novembre 2026 : la page publique s’ouvre (pas de 404) et affiche « forked from Friedrich-M/UniMate ». L’[API GitHub du dépôt LUXHIB](https://api.github.com/repos/LUXHIB/UniMate-Open-Source-AI-Animator) confirme `fork: true`, `parent.full_name` et `source.full_name` à `Friedrich-M/UniMate`, et `mirror_url: null`. C’est donc un **fork public**, distinct du [dépôt amont Friedrich-M/UniMate](https://github.com/Friedrich-M/UniMate), et non un miroir déclaré par GitHub. Aucune parité de révision ou de contenu n’a été établie ; la ligne UniMate du tableau décrit l’amont, pas le fork LUXHIB.

| Candidat | Code | Poids | Corpus / actifs | Aptitude au contrat, décision |
|---|---|---|---|---|
| CondMDI | [MIT](https://github.com/setarehc/diffusion-motion-inbetweening/blob/main/LICENSE) | [Checkpoints du README](https://github.com/setarehc/diffusion-motion-inbetweening#3-download-the-pretrained-models), variantes à masques d’images/articulations ; pas de licence autonome de checkpoint confirmée. | HumanML3D : le [MIT du dépôt](https://github.com/EricGuo5513/HumanML3D/blob/main/LICENSE) ne couvre pas les mouvements sources [AMASS](https://amass.is.tue.mpg.de/license.html), ni les actifs [SMPL](https://smpl.is.tue.mpg.de/modellicense.html). | Diffusion à ancres éparses, représentation HumanML3D 22 articulations, CLI de référence en positions XYZ. Conversion explicite vers les poses locales SAS requise. Référence Python 3.7, PyTorch 1.13.1, CUDA 11.7 ; aucun parcours tout-CPU mesuré ici. |
| MoLab | [MIT](https://github.com/JasonNero/MoLab/blob/main/LICENSE) ; le composant `models/condmdi` est présenté comme un fork de CondMDI. | [README MoLab](https://github.com/JasonNero/MoLab#license) : conditions propres à CondMDI et aux dépendances préentraînées ; parcours fourni limité à la recherche non commerciale. | HumanML3D/AMASS, actifs SMPL/SMPL-X selon le parcours ; l’exemple visuel Mixamo est distinct et non nécessaire pour appeler le worker. | [Worker documenté](https://github.com/JasonNero/MoLab/blob/main/models/condmdi/README.md#inference) : `packed_motion` → positions racine et rotations articulaires ; IK pieds optionnelle ≠ contacts SAS. FastAPI/Godot sont des couches séparées, inutiles pour un worker v1. L’adaptation de CondMDI ne confère pas de nouveaux droits sur les poids ; CPU non mesuré. |
| UniMate (Mou et al.) | [MIT](https://github.com/Friedrich-M/UniMate/blob/main/LICENSE). | [Carte des modèles](https://huggingface.co/Linzhan/UniMate) / [CC BY-NC 4.0](https://huggingface.co/Linzhan/UniMate/blob/main/LICENSE), distincte de MIT ; l’encodeur FLAN-T5 est un autre composant. | [UniML3D](https://huggingface.co/datasets/Linzhan/UniML3D) agrège Mixamo (conditions Adobe), Objaverse-XL (licence par objet) et Truebones ZOO (pack non redistribuable ; provenance de certains clips signalée comme incertaine dans la carte). | Flow matching multi-squelettes, 60 images à 30 fps, `--keep_frames` sur un clip de référence ; les quaternions locaux, sous-images et contacts SAS nécessitent un adaptateur. Python 3.10/PyTorch en amont, 74,1 M paramètres pour le débruiteur recommandé, sans l’encodeur texte ; CPU non mesuré. |

**Trois vérifications séparées avant toute livraison :** licence du code et notices des dépendances ; conditions d’utilisation et de redistribution du checkpoint exact (y compris les poids auxiliaires) ; droits sur les corpus, rigs, clips de démo et sorties destinées à être publiées. Un MIT sur le code n’autorise pas à redistribuer les poids ou les actifs. Pour UniMate, la carte précise que le CC BY-NC 4.0 ne couvre que les droits des auteurs sur leurs poids et ne garantit pas l’absence de droits tiers sur les poids ou les mouvements générés. Pour CondMDI, l’absence de licence de poids confirmée n’est pas une autorisation. Pour MoLab, la restriction de recherche non commerciale concerne le parcours préentraîné, pas la licence MIT du code pris isolément.

### Deux alternatives à UniMate, vérifiées sur documentation

- **[CondMDI](https://setarehc.github.io/CondMDI/)** : la page du projet présente le conditionnement sur des poses clés éparses ou partielles. Le [README de référence](https://github.com/setarehc/diffusion-motion-inbetweening#motion-synthesis) documente `sample.conditional_synthesis`, notamment `--edit_mode`, `--transition_length`, `--seed` et `--no_text`, avec une sortie en positions XYZ. La limite annoncée de 9,8 s pour `--motion_length` concerne la génération texte-vers-mouvement, pas une limite vérifiée pour l’inbetweening SAS.
- **[MoLab](https://github.com/JasonNero/MoLab/blob/main/models/condmdi/README.md#inference)** : alternative d’interface autour de CondMDI, pas un modèle indépendant. Le README décrit `InferenceWorker.infer(InferenceArgs)` et les champs `packed_motion`, `text_prompt`, `num_samples`, `editable_features`, `unpack_mode` et `foot_ik`. Il indique que `InferenceResult` expose `root_positions` et `joint_rotations` ainsi que les poses observées ; cela n’établit pas la conformité des sorties au contrat v1.

**MoLab et CondMDI — inconnues et mesures manquantes.** Aucun profil SAS n’a été arrêté : révision et checkpoint exacts, nombre de paramètres et taille des poids pour la variante retenue, réglages effectifs de diffusion et de guidage, et limites de durée, de cadence et de nombre d’ancres après conversion vers le v1 restent à établir. Les options et exemples amont ne sont pas des réglages validés pour SAS. Sur l’hôte audité, la faisabilité de bout en bout sur CPU et sur ROCm n’a pas été testée ; aucune latence, pointe RAM/VRAM, mesure de répétabilité, ni mesure d’écart aux ancres (dont les sous-images) ou aux contacts d’accessoires n’est disponible pour ces deux candidats. Les ancres verrouillées et les contacts doivent être imposés et contrôlés par le pipeline SAS (§ 5), pas supposés à partir du conditionnement ou de l’IK pieds.

Aucun des trois candidats tiers n’a été installé ni exécuté ; aucun poids ni corpus tiers n’a été téléchargé ou redistribué pour le banc. Pour le MLP : [code original GPL-3.0-or-later](../LICENSE), trajectoires originales générées à la volée (aucune capture, aucun rig tiers), poids initialisés puis entraînés de zéro sur place. Le [checkpoint original de démo](../demo/sprint4-planar.f32) est livré séparément du ZIP ; la procédure ci-dessous le régénère. NumPy (BSD-3-Clause) est une dépendance d’exécution, non embarquée dans l’extension.

## 2. Interface exacte du prototype

Un processus local par job et un environnement Python distinct par modèle, hors du Python de Blender. L’appelant fixe l’exécutable et les poids provisionnés à l’avance. Le worker ne connaît ni l’UI ni le projet Godot ; aucun téléchargement ou chargement de poids au démarrage de Blender, aucun accès réseau du worker MLP.

- **Commande** : `<python-numPy-absolu> -sEB <chemin-absolu>/sprint4_worker.py --weights <chemin-absolu>/weights.f32`. `cwd` = dossier temporaire privé et neuf du job. Aucun chemin Blender/Godot dans les données. Poids locaux provisionnés par l’appelant, jamais par la requête. Format du fichier : 61 080 octets, six tenseurs float32 little-endian contigus en ordre C : `(55,96)`, `(96,)`, `(96,96)`, `(96,)`, `(96,6)`, `(6,)`. Épingler l’empreinte si l’on veut rejouer exactement ce checkpoint ; le worker vérifie taille/valeurs, pas l’empreinte.
- **Entrée** : `request.json` UTF-8 (≤ 64 Kio), champs exacts `schema`, `version`, `request_id`, `fps`, `joints`, `anchors`, `sample_frames`, `contacts` du v1. Identifiant non vide, ≤ 128 caractères, unique par job côté appelant. Version entier `1`. Cadence rationnelle exactement 24 fps (p. ex. `[24,1]`). Les 2–6 ancres strictement ordonnées doivent couvrir **exactement 24 images** ; plages et ancres fractionnaires acceptées (hors collision après normalisation float32), `|frame| ≤ 1e6`. `sample_frames` énumère **toutes** les images entières libres strictement entre les extrêmes, triées. Pas d’extrapolation.
- **Profil de rig** : trois joints dans cet ordre : `root` (parent `null`, repos `[0,0,0]`), `hinge` (parent `0`, repos `[0.4,0,0]`), `tip` (parent `1`, repos `[0.3,0,0]`). Rotations de repos identité WXYZ. Translations locales absolues en mètres, seule celle de la racine est animée, au plus ±2 m par composante. Quaternions unitaires WXYZ autour de Z seulement, angles au plus ±1,5 rad. Ce sont des limites d’acceptation, pas une garantie de qualité ou de généralisation. Mapping explicite obligatoire, jamais basé sur le nombre de joints. **`contacts` doit être `[]`** : une demande de contact, même bien formée, est refusée plutôt qu’ignorée.
- **Sortie** : statut `0` et publication de `response.json` via fichier temporaire puis renommage. `{"schema":"sas.inbetween.response","version":1,"request_id":"<copié>","samples":[{"frame":1,"translations":[...],"rotations":[...]}]}`. Chaque pose est entièrement numérique, `[J,3]` et `[J,4]`, dans l’ordre du rig. Aucune ancre dans la réponse. Erreurs gérées : statut `2`, diagnostic sur stderr ; un autre statut non nul, un délai/une annulation ou une réponse invalide sont aussi des échecs côté appelant. Ne lire un résultat qu’après sortie réussie et revalidation indépendante ; ne pas réutiliser des dossiers. Pas de fallback implicite. Pas de champ `seed` dans v1 : l’inférence est déterministe.

Le modèle prend au plus six triplets d’information par ancre (pose à six canaux, temps absolu normalisé, temps relatif à la requête), plus un bit de présence par slot et le temps de requête. Deux couches `tanh` (96 neurones chacune) prédisent directement XYZ racine et trois angles locaux. Pas de blend de poses, de valeur de base interpolée ou de résidu ajouté à une interpolation. Apprentissage Adam avec rétropropagation NumPy sur des combinaisons originales de dérive, balancement et retour amorti. Les ancres sont verrouillées par l’appelant en conservant leurs clés, non par une égalité imposée à la sortie brute du réseau.

## 3. Mesures et reproduction

Linux x86-64, AMD Ryzen 5 3600 (6 cœurs / 12 threads), CPython 3.14.10, NumPy 2.5.5, BLAS/OMP limités à un thread, Blender 5.2.1 LTS pour le banc séparé. Aucun GPU utilisé. Entraînement seed **4072026**, validation seed **42024**, 10 000 lots de 192, **44,36 s** (mesure interne) ; MSE mixte sur 8192 exemples tenus à part **0,26335 → 0,01547**, pas une mesure de qualité en mètres. Entraînement et benchmark utilisent des seeds différentes, mais la même famille synthétique.

- Banc CLI seed **4403**, 40 clips jamais vus (8 par nombre d’ancres de 2 à 6), 40 vrais processus. **97,1 ms** médians de bout en bout (87,2–111,5 ms), **43 284 Kio** RSS de pointe mesurée par `RUSAGE_CHILDREN` après les 40 appels (interpréteur et bibliothèques inclus). Noyaux seuls, hors préparation/JSON/démarrage : médianes MLP **0,283 ms**, Linear **0,329 ms**, Smooth **0,314 ms** ; pas une preuve de gain produit, ni une mesure de RAM de ces modes intégrés.
- Démo quatre ancres `[0,6.5,15,24]` : translations **`(22,3,3)`**, rotations **`(22,3,4)`**, tout fini, quaternions unitaires, aucune ancre écrite. Rejouée à l’octet. En décalant uniquement le Y de l’ancre `6.5` de −0,4 m, le Y généré change jusqu’à **0,402 m** ; écart maximal de racine à Linear/Smooth **0,205 / 0,242 m** sur la démo initiale. C’est une preuve de conditionnement et de sortie distincte, pas de qualité. Le script exerce aussi les refus de version/champs, double clé, ancre dupliquée, non-finitude, rig, quaternion, poids corrompus et contacts non pris en charge.
- SHA-256 des poids : `d58e82403486b34854013eebea007af6c8b9bb339146ecfa76ed99efe993801a`. SHA-256 de la réponse de démo : `640ad0add1f914786706823a2f618ab208a6f666795bda2ac20a67f643c27711` ; identité binaire constatée sur cette pile, pas garantie entre BLAS/machines.

RMS sur les mêmes échantillons libres, `CPUBackend` SAS réel, pas une réimplémentation :

| Mode | Racine XYZ (m) | Angle local RMS (rad) | Position du bout de chaîne après FK (m) |
|---|---:|---:|---:|
| MLP | 0,211 | 0,136 | 0,238 |
| Linear | **0,189** | **0,121** | **0,213** |
| Smooth | 0,191 | 0,124 | 0,216 |

**Blender headless, fixture `k4-00` uniquement :** transpose explicitement les trois joints en canaux locaux du rig de test et bake les échantillons libres sur une copie. Clés aux ancres (dont sous-images) et matrices évaluées **exactement égales** pour les trois modes. Action, slot et temps courant restaurés ; brouillons supprimés. L’accessoire est un Empty animé par la vérité synthétique, pas passé au modèle ni déplacé par le bake. Tolérances 5 mm et 0,02 rad. Maxima position/orientation de prise : MLP **0,454 m / 0,169 rad**, Linear **0,120 m / 0,105 rad**, Smooth **0,168 m / 0,110 rad**, tous rejetés ; le contrôle vérité passe (< 0,2 µm, 0 rad mesuré). Un décalage contradictoire de 0,1 m à une ancre fractionnaire est diagnostiqué sans bouger l’ancre. Ce banc mesure un résidu sur le rig évalué, **pas** une résolution de contacts, ni un contact pied-sol.

### Poids et démo : régénérer puis vérifier

Depuis la racine du dépôt (Bash, Linux), créer un espace privé neuf sous `/tmp/opencode` et entraîner localement le checkpoint. Sur cet hôte, le venv dédié réutilise NumPy déjà présent via `--system-site-packages` : ce n’est pas un environnement hermétique. Pile vérifiée : CPython **3.14.10**, NumPy **2.5.5**, bibliothèques CPU `libblas`/`libcblas` **3.12.0**, réglages BLAS/OMP à un thread comme ci-dessous. Sur un autre hôte, provisionner NumPy à part dans le venv si nécessaire, jamais au lancement de Blender. Aucune installation n’est requise sur l’hôte audité.

```bash
src="$PWD/backends/inbetweening"
run="$(mktemp -d /tmp/opencode/sas-sprint4.XXXXXXXX)" && (
  set -euo pipefail
  cd "$run"
  export TMPDIR="$run" OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
  python3 -m venv --without-pip --system-site-packages "$run/venv"
  py="$run/venv/bin/python"
  "$py" -sEB -c 'import sys, numpy as np; print(sys.version); print(np.__version__); np.show_config()'
  "$py" -sEB "$src/sprint4_train.py" weights.f32
  printf '%s  %s\n' d58e82403486b34854013eebea007af6c8b9bb339146ecfa76ed99efe993801a weights.f32 | sha256sum -c -
  "$py" -sEB "$src/sprint4_bench.py" "$run/weights.f32" proof
  printf '%s  %s\n' 640ad0add1f914786706823a2f618ab208a6f666795bda2ac20a67f643c27711 proof/demo/response.json | sha256sum -c -
)
```

La démo CLI est dans `$run/proof/demo/{request,response}.json`, le bilan dans `$run/proof/metrics.json`. Pour rejouer le worker seul, copier uniquement `request.json` dans un répertoire de job neuf et passer `--weights "$run/weights.f32"` avec le même Python et les mêmes réglages. Vérifier le SHA-256 des poids **avant** l’appel : le worker ne le fait pas. Si un hash diffère, ne pas présenter le résultat comme une reproduction à l’octet ; consigner la pile et les deux nouveaux hashes et revalider les métriques. Le hash identifie les octets, pas une licence ni la qualité du mouvement.

Contrôle Blender séparé, optionnel, sur la fixture `k4-00` (pas nécessaire pour produire la démo CLI) :

```bash
(
  set -euo pipefail
  unset BLENDER_USER_CONFIG BLENDER_USER_SCRIPTS BLENDER_USER_EXTENSIONS BLENDER_USER_DATAFILES
  export TMPDIR="$run" XDG_CACHE_HOME="$run/cache" BLENDER_USER_RESOURCES="$run/blender-profile"
  mkdir "$BLENDER_USER_RESOURCES"
  blender --background --factory-startup --offline-mode --python-exit-code 1 \
    --python "$src/sprint4_blender.py" -- "$run/proof/k4-00"
)
```

Réexécution sur l’hôte audité : deux SHA-256 identiques à la référence, 40 jobs CLI, banc `k4-00` rejoué sous Blender 5.2.1 LTS avec le même verdict. Artefacts temporaires de ce passage : `/tmp/opencode/sas-doc-check.fg9sTwpP` (`weights.f32`, `proof/metrics.json`, `proof/demo/{request,response}.json`, `proof/k4-00/blender-metrics.json`). Build et suite headless existants réussis sur une copie temporaire de `HEAD`, avec installation de test de l’extension dans un profil Blender jetable uniquement. Le dépôt `blender_org` introuvable a produit un diagnostic à la désinstallation, mais la suppression réelle de l’extension a été vérifiée. Le banc MLP a été exécuté à part ; pas de test Godot optionnel.

## 4. Audit HIP indépendant du MLP

- **Hôte observé :** Linux x86-64, noyau `7.3.5-2-cachyos`, Ryzen 5 3600 (6 cœurs / 12 threads), Radeon RX 6600 (Navi 23, PCI `1002:73ff`, pilote `amdgpu`). `rocminfo` et les propriétés retournées par HIP identifient `gfx1032`. `hipcc --version` annonce HIP `7.16.0-4f6d503` et AMD clang `24.0.0git` ; `hipRuntimeGetVersion` renvoie `71600000`. Les paquets `hip-runtime-amd` et `rocm-core` portent la version `10.1-2.1` : c’est la version des paquets, pas celle de l’API HIP.
- **Preuve d’exécution :** source originale `probe.hip` et binaire conservés seulement dans `/tmp/opencode/sas-hip.NATrsAHW`. Compilation `hipcc --offload-arch=gfx1032 -O2 probe.hip -o probe` ; exécution `timeout 15 ./probe`. Environnement vidé (`env -i`, uniquement `PATH=/opt/rocm/bin:/usr/bin`, `HOME` et `TMPDIR` pointant sur le dossier de la sonde, `LC_ALL=C`), donc sans `HSA_OVERRIDE_GFX_VERSION`. Ni installation, ni privilège élevé, ni modification système. La sonde a détecté un seul GPU, alloué les buffers, copié les entrées vers le GPU, lancé une addition vectorielle entière, synchronisé, recopié la sortie et comparé chaque valeur à la référence CPU : **1 024 / 1 024 sommes exactes**, code de sortie `0`.
- **Conclusion :** HIP natif fonctionne pour ce noyau sur cette pile installée, au-delà de la seule détection matérielle. La RX 6600 / `gfx1032` et CachyOS ne sont pas répertoriés dans la [matrice AMD ROCm 10.1](https://rocm.docs.amd.com/en/docs-10.1.0/compatibility/compatibility-matrix.html). Ce résultat ne constitue pas un support officiel ni une validation de rocBLAS, MIOpen, PyTorch ROCm, des trois candidats tiers ou de performances GPU. **Le MLP reste CPU NumPy et n’utilise pas ROCm** ; la sonde ne change ni les résultats du banc ni la décision de branchement.

## 5. Conditions avant activation

**Les poses verrouillées et les contacts avec les accessoires sont des invariants à faire respecter par le pipeline d’animation, pas des garanties du MLP.** Un futur adaptateur doit conserver les ancres dures, résoudre les contacts via le solveur SAS, vérifier les résidus de position et d’orientation après évaluation et rejeter la copie si une cible ne peut pas être satisfaite — sans déplacer l’accessoire, sans écraser l’ancre et sans changer l’action source.

Le lancement, l’annulation et le délai de l’adaptateur planaire sont couverts par les tests du processus ; le [GLB de la vraie sortie MLP](../demo/sprint4-planar-README.md) a été relu. Restent hors périmètre ou non validés : retargeting général/Rigify, correction des contacts, séquences représentatives et glissement pied-sol, recette interactive et Windows. L’horizon fixe, le petit rig plan et la chute de qualité sur certaines répartitions d’ancres excluent un branchement en production.
