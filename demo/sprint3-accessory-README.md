# Sprint 3 — Balise Quartz

[`sprint3-accessory.blend`](sprint3-accessory.blend) est la scène source éditable : mannequin synthétique de [`reference_character.py`](../scripts/reference_character.py), rig Rigify et nouvelle balise facettée à deux matériaux, sans asset importé. La scène reprend le scénario de la [fixture d’export](../tests/headless_accessories.py) : main droite en IK, transformation de rig non triviale, accessoire rigide à pivot décentré par rapport à ses bornes et échelle non uniforme en scène. Elle s’ouvre à l’image 16. Le contact est actif de 11 à 20 inclus ; aux images 21–22, la balise reprend son transform libre. Le saut au relâchement est voulu pour exercer la fin du contact ; il ne simule pas une chute.

| Fichier | Contenu | Octets |
|---|---|---:|
| [`sprint3-accessory-combined.glb`](sprint3-accessory-combined.glb) | Mannequin skinné et balise rigide sous `DEF-hand.R`, 1 clip | 1 337 964 |
| [`sprint3-accessory-separate.glb`](sprint3-accessory-separate.glb) | 1 maillage rigide au pivot local, transform identité, sans skin ni animation | 5 868 |
| [`sprint3-accessory-combined.glb.json`](sprint3-accessory-combined.glb.json) | Référence Blender pour le banc Godot, mesures et SHA-256 | |

## Reproduire

Linux, Blender **5.2.1 LTS** (`9e2066aef7ef`), Python **3.13.13** intégré, Rigify local **0.6.10**, Godot **4.7.2 stable**. Depuis la racine du dépôt :

```bash
scratch=$(mktemp -d)
mkdir "$scratch/build" "$scratch/replay"
unset BLENDER_USER_CONFIG BLENDER_USER_SCRIPTS BLENDER_USER_EXTENSIONS BLENDER_USER_DATAFILES
export BLENDER_USER_RESOURCES="$scratch/profile"

build_demo() {
  blender -b -t 8 --factory-startup --offline-mode --disable-autoexec \
    --python-exit-code 1 --python scripts/build_sprint3_accessory_demo.py -- "$@"
}

build_demo --output "$scratch/build"
build_demo --output "$scratch/replay" --from-blend demo/sprint3-accessory.blend
for dir in "$scratch/build" "$scratch/replay"; do
  for suffix in combined.glb separate.glb combined.glb.json; do
    cmp "demo/sprint3-accessory-$suffix" "$dir/sprint3-accessory-$suffix"
  done
done

bash tests/test_godot_import.sh \
  demo/sprint3-accessory-combined.glb \
  demo/sprint3-accessory-combined.glb.json \
  demo/sprint3-accessory-separate.glb
```

Le script appelle `bpy.ops.sas.export_glb` en modes Sprint 3 `COMBINED` et `ACCESSORY`. Les deux GLB et le JSON ont donné des fichiers identiques octet par octet à la reconstruction depuis la fixture et au réexport depuis le `.blend` livré. Cette mesure ne porte pas sur le binaire `.blend` lui-même. Le banc existant `tests/test_godot_import.sh` lance `tests/godot_probe.gd` en headless dans un projet temporaire isolé.

## Mesures headless

- Blender : 22 800 sommets pour le mannequin, 37 sommets et 37 faces pour la balise. Déplacement du pivot de **210,84 mm** entre les images 11 et 16 ; saut relatif à `DEF-hand.R` de **313,96 mm** entre 20 et 21.
- Godot : **35 os, 35 binds**, poids contrôlés, clip de **0,458333 s** qui anime `DEF-upper_arm.R`. Dimensions de l’AABB du mannequin : **1,491 × 1,779 × 0,732 m** en XYZ Godot. La balise importée est sous un `BoneAttachment3D` ciblant `DEF-hand.R` ; sa géométrie séparée a les mêmes bornes locales et peut reprendre le pivot animé à identité.
- Écarts maximaux sur **12 échantillons à 24 i/s** :

| Transform comparé à Blender | Position | Base (écart max d’un vecteur) |
|---|---:|---:|
| Balise, monde | 0,183 mm | 0,001123 |
| Balise, relatif à la main DEF | 10,396 mm | 0,043718 |
| Main DEF, monde | 4,799 mm | 0,035775 |

Tous les écarts passent les seuils de la sonde existante. Ces vérifications portent sur l’import, les liaisons, les bornes et les transforms aux images échantillonnées. Aucun rendu, contrôle graphique ou essai interactif dans le jeu n’a été réalisé.

Le ZIP Sprint 3 a été construit avec `scripts/build_extension.sh` dans une copie de travail temporaire. `SOULS_TEST_GODOT=1 bash scripts/test_headless.sh` y a passé les tests smoke et workflow, puis s’est arrêté sur `test_planted_feet_follow_moving_target_and_release` : le test attend un libellé commençant par `Left Foot:`, le code renvoie `Left Foot · All frames · 100% influence`. Les deux tests de `headless_accessories.py`, lancés séparément sur le ZIP installé dans un profil isolé, et les sondes Godot de leur fixture et des GLB livrés ont réussi. Blender a émis un avertissement d’armature lors de l’export séparé ; le contrôle du GLB et l’import Godot confirment que cet asset ne contient ni skin ni armature.

Maillages et poses SAS : [GPL-3.0-or-later](../LICENSE). Données de rig générées par Rigify 0.6.10, © Blender Foundation et contributeurs, GPL-2.0-or-later ([sources Blender](https://projects.blender.org/blender/blender)), réutilisées sous GPL-3.0-or-later ; aucun code Python Rigify embarqué.
