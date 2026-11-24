# Étude des solutions externes — Sprint 0

Pour les moteurs IA : revue de documentation et de métadonnées uniquement. Aucun poids, modèle corporel ou jeu de données n’a été installé, exécuté ou redistribué. Une licence de code ne vaut pas licence des poids ni des données.

## Rigging

### Rigify — référence locale

[Manuel Blender](https://docs.blender.org/manual/en/latest/addons/rigify/index.html) · [Historique du paquet](https://extensions.blender.org/add-ons/rigify/versions/)

La version 0.6.10 fournie par le Blender local (`scripts/addons_core/rigify`) et le paquet historique sont indiqués GPL-2.0-or-later. Rigify génère les contrôleurs d’un rig, notamment IK/FK, à partir d’un **métarig ajusté au personnage**. Il ne détecte pas les articulations du maillage à lui seul. Le skinning automatique de Blender est une opération distincte et peut échouer sur des géométries problématiques. Pas de GPU CUDA ni de poids IA nécessaires. Ne pas copier Rigify dans le ZIP ; tester d’abord le flux métarig ajusté → rig → os de déformation. Un essai séparé a généré le rig standard sans maillage : voir [le bilan](sprint-0.md).

### UniRig — détection et skinning, CUDA

[Code et installation](https://github.com/VAST-AI-Research/UniRig) · [Licence du code](https://github.com/VAST-AI-Research/UniRig/blob/main/LICENSE) · [Poids Hugging Face](https://huggingface.co/VAST-AI/UniRig/tree/36842e2b5947e9e60f89275b83208c8e74071c63) · [Données Articulation-XL2.0](https://huggingface.co/datasets/Seed3D/Articulation-XL2.0)

Code MIT. La carte des poids annonce MIT et l’arbre à la révision ci-dessus contient des checkpoints squelette **et** skinning, malgré une description contradictoire sur la disponibilité du skinning. Le jeu Articulation-XL2.0 annonce CC BY 4.0 ; vérifier séparément les droits des maillages sources et de chaque fichier avant redistribution.

L’installation amont cible Python 3.11, PyTorch testé à partir de 2.3.1, NumPy 1.26.4 et un GPU CUDA avec au moins 8 Go de VRAM d’après le README. Les [dépendances](https://github.com/VAST-AI-Research/UniRig/blob/main/requirements.txt) incluent `bpy==4.2`, `flash_attn`, et la procédure ajoute `spconv` et les extensions PyG `torch_scatter`/`torch_cluster`. Verrouiller des versions compatibles Python/PyTorch/CUDA dans un environnement **hors de Blender**. Aucun chemin CPU ou Intel n’est validé ici. L’étape squelette et l’étape de skinning sont distinctes ; les noms, la hiérarchie humanoïde, les contrôleurs et l’export restent à traiter par l’extension.

## Inbetweening

### CondMDI — candidat recherche pour les poses clés éparses

[README et poids](https://github.com/setarehc/diffusion-motion-inbetweening) · [Licence du code](https://github.com/setarehc/diffusion-motion-inbetweening/blob/main/LICENSE) · [HumanML3D](https://github.com/EricGuo5513/HumanML3D) · [AMASS](https://amass.is.tue.mpg.de/license.html) · [SMPL](https://smpl.is.tue.mpg.de/modellicense.html)

Code MIT. Les poids Google Drive sont entraînés sur HumanML3D ; aucune licence autonome des poids ni droit de redistribution commerciale n’a été confirmé. HumanML3D s’appuie notamment sur AMASS et ne redistribue pas directement ces mouvements. Les licences consultées pour AMASS et le modèle SMPL limitent les usages et la redistribution ; ne pas les inclure dans une livraison GPL ni présumer que MIT autorise un usage commercial de toute la chaîne.

Configuration amont : Ubuntu 20.04, Python 3.7, CUDA 11.7, PyTorch 1.13.1 ; selon le chemin, CLIP, spaCy, FFmpeg, assets SMPL, statistiques HumanML3D et éventuellement PyTorch3D. Python 3.7 est en fin de vie : ne pas l’utiliser dans le Python Blender ni comme dépendance globale. Les exemples conditionnent des images ou articulations dans une représentation HumanML3D (22 articulations) et renvoient notamment des positions 3D ; ils ne fournissent pas directement les courbes de contrôle d’un personnage Blender. La préservation exacte après retargeting, les pieds et les contacts d’accessoires doivent être testés en aval. Les performances CPU et Intel ne sont pas établies.

### MDM — alternative ouverte au niveau du code

[README et poids](https://github.com/GuyTevet/motion-diffusion-model) · [MIT](https://github.com/GuyTevet/motion-diffusion-model/blob/main/LICENSE) · [Environnement](https://github.com/GuyTevet/motion-diffusion-model/blob/main/environment.yml)

MDM propose un mode `in_between` pour des données text-to-motion, plus adapté à une plage intermédiaire qu’à des poses arbitraires multiples. Les poids en 50 étapes sont un candidat à mesurer, pas une garantie de performances locales. La configuration de référence fixe Python 3.7, PyTorch 1.7.1 et CUDA 11.0 ; elle demande aussi des dépendances selon le modèle (CLIP, spaCy, SMPL, FFmpeg). Les droits des poids et des données restent distincts du MIT. Aucun essai d’inférence effectué.

### Décision

Démarrer avec Rigify comme référence de rigging et une **interpolation déterministe CPU** comme référence d’animation (pas de l’IA). Reporter l’évaluation de UniRig à une machine CUDA compatible, et celle de CondMDI/MDM à un environnement isolé dont les dépendances, l’usage et les poids auront été validés. Aucun backend par défaut au Sprint 0.
