# Souls Animation Studio 0.5.0

Extension Blender libre : **Quick Rigging T/A sur CPU**, édition de poses Rigify, contacts, bibliothèque de poses, interpolation CPU et export GLB. Le parcours principal fonctionne hors ligne. **AI Inbetweening expérimental** exécute un vrai modèle neuronal sur CPU, limité à une chaîne FK planaire de trois os. Il ne prend pas en charge les humanoïdes Rigify. UniRig, séparé, reste expérimental et non validé en inférence. Aucun poids ni téléchargement au démarrage.

## Installer et essayer

1. Dans Blender **5.2** (testé avec **5.2.1 LTS**), ouvrir **Édition → Préférences → Obtenir des extensions → Installer depuis le disque**, et choisir [`dist/souls_animation_studio-0.5.0.zip`](dist/souls_animation_studio-0.5.0.zip). Le ZIP se reconstruit avec `bash scripts/build_extension.sh`.
2. Dans la vue 3D, appuyer sur `N` et ouvrir **Souls Animation Studio**. Pour générer un rig, **Rigify** doit être disponible sous le module local `rigify` (c’est le cas du paquet Blender Arch testé). L’extension n’installe rien.
3. Ouvrir le [banc de skinning](demo/sprint3-skinning.blend) et ses [comparaisons CPU](demo/sprint3-skinning-README.md), ou la [démo accessoire](demo/sprint3-accessory.blend) et ses GLB [combiné](demo/sprint3-accessory-combined.glb) / [séparé](demo/sprint3-accessory-separate.glb). Les démos [Sprint 1](demo/sas-humanoid.blend) et [Sprint 2](demo/sas-combat.blend) restent disponibles.

- [Guide utilisateur](docs/guide-utilisateur.md)
- [Sprint 4 : utiliser et évaluer le modèle neuronal](docs/sprint-4.md) · [Démonstration Blender et GLB](demo/sprint4-planar-README.md)
- [Recette interactive et rapport Sprint 3](docs/sprint-3.md)
- [Étude IA et licences](docs/etude-backends.md) · [Adaptateur UniRig](backends/unirig/README.md) · [Contrat AI Inbetweening](backends/inbetweening/contract-v1.md)
- [Guide de l’éditeur de poses et des contacts](docs/pose-editor.md)
- [Procédure de validation visuelle](docs/validation-visuelle.md)
- [Rapport Sprint 1](docs/sprint-1.md)
- [Rapport Sprint 2](docs/sprint-2.md)
- [Architecture et licences](docs/architecture.md)
- [Permissions OpenCode du projet](docs/opencode.md)

## Développer et tester

Depuis la racine, avec Bash, Blender et Python système 3.11+ :

```sh
bash scripts/build_extension.sh
bash scripts/test_headless.sh
# Avec Godot installé, pour vérifier les deux imports dans des projets temporaires :
SOULS_TEST_GODOT=1 bash scripts/test_headless.sh
```

Les tests installent le ZIP hors ligne dans un profil jetable. `BLENDER_BIN` permet de choisir le binaire et `TMPDIR` le répertoire temporaire. Pour tester un autre ZIP : `bash scripts/test_headless.sh /chemin/extension.zip`.

Pour régénérer les deux artefacts de démonstration sans modifier les préférences utilisateur :

```sh
profile=$(mktemp -d)
BLENDER_USER_RESOURCES="$profile" blender --background --factory-startup \
  --offline-mode --disable-autoexec --python-exit-code 1 \
  --python scripts/build_demo.py -- "$PWD/demo"
rm -rf -- "$profile"
```

Licence du projet et du personnage synthétique : [GPL-3.0-or-later](LICENSE). Les fichiers Python de Rigify, les moteurs externes et les poids ne sont pas distribués dans le ZIP.
