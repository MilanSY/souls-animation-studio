# Rapport — Sprint 1, extension 0.2.0

Base : `70a2c2d` (Sprint 0). Cible de validation : Omarchy 4.5.0 / Arch Linux, Intel HD Graphics 520, Mesa 26.2.5, Blender 5.2.2 LTS (`d13f752e3b9c`), Python 3.14.8, Rigify intégré 0.6.10, Godot 4.7.2 stable. Le rigging, le calcul de poids, l’interpolation, l’export et les sondes ont été exécutés hors ligne sur CPU ; le renderer interactif de Godot n’a pas été évalué.

## Livrés et preuves

| Périmètre | Résultat |
| --- | --- |
| Personnage et pose de repos | Mannequin synthétique A-pose, **22 800 sommets**, une seule composante fermée, hauteur **1,779 m**. Le fichier `.blend` contient le maillage riggé, quatre poses de référence et la copie interpolée Smooth (1–37 à 24 i/s). |
| Quick Rigging | Métarig Basic Human, estimation par proportions/sections du maillage, ajustement manuel suivi de génération Rigify, voie All-in-One, contrôles de validité et remise à zéro. Le traitement ne touche pas à la position des sommets, au parent ni aux transformations du maillage. |
| Contrôleurs et skinning | IK bras/jambes, FK, bassin, colonne, tête, pieds/mains ; seules les **35 DEF** déforment. Sur le mannequin, **1 à 7** influences positives par sommet, aucune manquante, toutes normalisées. La surface évaluée se déforme sous les contrôles. Skinning linéaire, pas de segments B-Bone multiples, pour l’interopérabilité. |
| CPU Inbetweening | Linear et Smooth, SLERP sur l’arc court pour Euler/quaternion, sélection des clés ou plage, Action séparée. Les tests ciblés sur un vrai Rigify comparent exactement les matrices évaluées aux poses verrouillées, dont une pose sur une sous-image. |
| Export GLB | Copie jetable DEF, échantillonnage des contraintes/contrôleurs à chaque image, clip démarrant à t=0. Import de la démo et de l’export QA dans des projets Godot temporaires : un mesh, un squelette, **35 joints et 35 binds**, poids valides, axes et échelle cohérents. Le clip de démo dure **1,5 s** et anime effectivement `DEF-upper_arm.L`. Aucun contrôleur Rigify dans les joints exportés. |

La sonde `scripts/compare_demo.py` recompte la déformation linéaire à partir des poids, des matrices de bind et des animations du GLB, puis compare la surface au graphe évalué de Blender. Sur sept images de la démo, l’écart maximal est **~1,1 µm** au repos et **~10 mm** en mouvement, avec une moyenne par image au plus **~1,6 mm**. Ce n’est pas une promesse d’équivalence exacte des poses cisaillées : glTF stocke des TRS sans cisaillement.

Une régénération du mannequin, du rig et des deux fichiers de démonstration dans un profil froid a pris **~6 s** sur la machine de test. Le GLB régénéré était identique octet par octet à la référence livrée. Cette mesure ne couvre pas la fluidité du viewport.

## Reproduction

```sh
bash scripts/build_extension.sh
SOULS_TEST_GLB=/tmp/opencode/sas-qa.glb bash scripts/test_headless.sh
bash tests/test_godot_import.sh /tmp/opencode/sas-qa.glb
bash tests/test_godot_import.sh demo/sas-humanoid.glb
python3 backends/inbetweening/test_cpu.py

profile=$(mktemp -d)
BLENDER_USER_RESOURCES="$profile" blender --background --factory-startup \
  --offline-mode --disable-autoexec --python-exit-code 1 \
  --python backends/inbetweening/test_blender.py
BLENDER_USER_RESOURCES="$profile" blender --background --factory-startup \
  --offline-mode --disable-autoexec demo/sas-humanoid.blend --python-exit-code 1 \
  --python scripts/compare_demo.py
rm -rf -- "$profile"
```

Les **10 tests** d’extension installent le vrai ZIP dans un profil isolé et reproduisent les garanties Sprint 0 sur le manifeste et le cycle de vie. Les **8 tests** ciblés couvrent en complément le moteur CPU et l’adaptateur Blender. La démo a également passé l’import **`godot --headless --editor --import`** dans un projet jetable ; le `PackedScene` importé a été instancié et son os DEF a bougé sous la lecture du clip. Le projet du jeu n’a pas été ouvert ni modifié.

La **validation indépendante** a rejoué la construction, les 18 tests, les deux sondes `GLTFDocument` et la comparaison de la surface GLB. Elle a vérifié les 19 entrées du ZIP, l’absence de code tiers embarqué et le rejet des entrées invalides (dont ZIP incomplet ou corrompu et plage inversée). Aucun blocage headless relevé.

## Revue visuelle et problèmes rencontrés

- [Planche de revue](../demo/visual-review.jpg) : rendus Cycles CPU, images **1, 13, 25, 37** de gauche à droite, vue avant puis trois-quarts. Les coudes et les genoux plient, la silhouette rejoint sa pose neutre, sans rupture visible à ces images. Plis et léger pincement (notamment autour des bras/aisselles en garde) restent visibles. Cette revue d’images fixes n’est pas une recette interactive, une preuve d’absence d’intersection, ni une validation du contact au sol.
- L’estimation initiale rejetait le profil d’épaule de la fixture ; les sections ont été corrigées. Bone Heat produisait de nombreux totaux non unitaires : le binder normalise les lignes valides sans supprimer de poids positifs et refuse les sommets sans influence, au lieu de leur assigner un poids arbitraire.
- Le premier clip provoquait un cisaillement excessif sur `DEF-hand.R` à l’image 17. Le clip livré désactive **IK Stretch**. L’export refuse les poses au-delà de sa tolérance, plutôt que d’écrire un résultat manifestement divergent. Le paquet Blender signale également l’absence de MeshOptimizer ; la compression n’est pas utilisée et les GLB non compressés passent les vérifications.
- Non validés : fluidité du viewport sur l’Intel HD 520, gestes interactifs de la sidebar/sélecteur de fichiers et séquence interactive Undo/Redo, rendu et intégration visuelle dans Godot, qualité sur des personnages tiers, vêtements séparés, mains articulées, amplitudes extrêmes et toutes les variantes d’échelle. Le test ponctuel de `bpy.ops.ed.undo` a été refusé par le contexte headless ; l’enregistrement `UNDO` des opérateurs n’est pas une validation de cette interaction. Voir la [procédure de validation visuelle](validation-visuelle.md).

## Recommandations Sprint 2

1. Faire une recette interactive sur la HD 520 (Undo/Redo, visibilité des contrôleurs, métriques viewport) et vérifier la pose sur davantage de silhouettes avec des transformations non uniformes.
2. Améliorer la répartition des poids et mesurer la conservation des volumes aux épaules, coudes et genoux ; ajouter des poses de contact et des tests de glissement/intersection.
3. Mesurer les erreurs de cuisson GLB par scène, et traiter explicitement root motion, clips multiples et retarget Godot dans un projet de validation dédié.
4. Garder l’interface numérique indépendante de Blender avant d’étudier CondMDI dans un processus local isolé ; vérifier les licences et les coûts CPU sur le matériel cible avant toute intégration.
