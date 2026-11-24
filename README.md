# Souls Animation Studio 0.2.0

Extension Blender libre et hors ligne : **Quick Rigging** pour humanoïdes en A-pose, skinning automatique, **CPU Inbetweening** et export GLB expérimental vers Godot. L’ajustement est géométrique et l’interpolation est numérique : aucune fonctionnalité IA, aucun poids, aucun téléchargement au démarrage.

## Installer et essayer

1. Dans Blender **5.2.2 LTS**, ouvrir **Édition → Préférences → Obtenir des extensions → Installer depuis le disque**, et choisir [`dist/souls_animation_studio-0.2.0.zip`](dist/souls_animation_studio-0.2.0.zip). Le ZIP se reconstruit avec `bash scripts/build_extension.sh`.
2. Dans la vue 3D, appuyer sur `N` et ouvrir **Souls Animation Studio**. Pour générer un rig, **Rigify** doit être disponible sous le module local `rigify` (c’est le cas du paquet Blender Arch testé). L’extension n’installe rien.
3. Ouvrir [`demo/sas-humanoid.blend`](demo/sas-humanoid.blend), puis lire l’animation de 1 à 37. [`demo/sas-humanoid.glb`](demo/sas-humanoid.glb) contient le même exemple cuit sur les os de déformation.

- [Guide utilisateur](docs/guide-utilisateur.md)
- [Procédure de validation visuelle](docs/validation-visuelle.md)
- [Rapport Sprint 1](docs/sprint-1.md)
- [Architecture et licences](docs/architecture.md)

## Développer et tester

Depuis la racine, avec Bash, Blender et Python système 3.11+ :

```sh
bash scripts/build_extension.sh
bash scripts/test_headless.sh
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
