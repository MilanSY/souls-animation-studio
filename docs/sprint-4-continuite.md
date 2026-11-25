# Sprint 4 — Continuité du clip planaire entraîné

**Sur ce clip, Linear suit le mieux la référence à 24 Hz.** Smooth réduit les changements de vitesse immédiatement autour des deux ancres intérieures, mais s’écarte davantage de la référence sur la seconde entière. Le MLP présente les plus forts pics d’accélération à 24 Hz sur les trois signaux mesurés : conserver exactement une pose verrouillée ne garantit pas un raccord de vitesse.

## Données et méthode

- **Un seul clip**, inédit pour l’entraînement (seed du clip `4403`, seed d’entraînement `4072026`), de l’image 1 à 25, soit 1 s à 24 i/s. Le [JSON de preuve](../demo/sprint4-planar-preuve.json) fournit la requête v1, la réponse MLP archivée et la trajectoire synthétique indépendante (`reference_samples`). Linear et Smooth sont recalculés par le vrai `CPUBackend` de l’extension : translation affine et SLERP au plus court, avec un paramètre temporel `3u²−2u³` pour Smooth.
- La sonde vérifie l’empreinte SHA-256 du [checkpoint livré](../demo/sprint4-planar.f32), puis rejoue le [worker](../backends/inbetweening/sprint4_worker.py) en processus Python local, hors Blender, dans un dossier de travail temporaire. Sur la pile testée, l’écart maximal entre les 22 poses libres rejouées et archivées est **0 m** par composante XYZ et **0 rad** par angle local (tolérance de la sonde : `2e-6` par composante). Les mesures utilisent la réponse archivée. Après vérification de la concordance des ancres de la référence avec la requête, les quatre pistes utilisent exactement les mêmes poses verrouillées à **1, 7,5, 16 et 25** ; elles ne sont pas prédites par le modèle.
- Signaux : position XYZ de la **racine**, position XYZ de la **tête du dernier joint** (`tip`, pas la queue de l’os ni un sommet du skin) par FK canonique avec segments de 0,4 et 0,3 m, et vecteur des **trois angles locaux Z déroulés** en radians. L’angle du dernier joint ne déplace pas sa propre tête ; la mesure angulaire le couvre néanmoins. Pour ce vecteur, on mesure une norme sur trois canaux, pas une rotation globale ni une moyenne par joint.
- **Grille uniforme 24 Hz :** 25 poses aux images entières, 24 vitesses et 23 accélérations. Pour un signal vectoriel `x`, `v = (x_droite − x_gauche)/Δt` sur un intervalle et `a = (v_après − v_avant)/((Δt_avant + Δt_après)/2)` au nœud commun. La vitesse est la norme de `v`. RMS = racine de la moyenne des normes au carré ; l’erreur RMS par rapport à la référence porte sur la **différence des vecteurs**, pas sur la différence de leurs normes.
- **Voisinage des ancres :** même cadence de base, mais on réinsère la clé exacte à 7,5. Les intervalles `7 → 7,5` et `7,5 → 8` durent chacun `1/48 s` (le reste du clip n’est pas rééchantillonné à 48 Hz). La grille uniforme, elle, passe directement de 7 à 8 et peut masquer un raccord. À 16, les deux pas valent `1/24 s`. Aux extrémités, seule la vitesse du côté disponible est mesurée ; aucune accélération n’est extrapolée.

## Résultats à 24 Hz

Pour les positions, vitesses en m/s et accélérations en m/s² ; pour les angles locaux, rad/s et rad/s². Chaque cellule dérivée indique **RMS / pic / erreur RMS vs référence**. Les erreurs de pose portent sur les 22 images libres ; les RMS canoniques MLP et Linear à la racine et au dernier joint concordent avec les mesures Blender de la preuve à `2e-6 m` près.

| Signal | Variante | Vitesse | Accélération | Image du pic d’accél. |
|---|---|---:|---:|---:|
| Racine | Référence | 1,349 / 2,396 / — | 4,738 / 7,322 / — | 10 |
| Racine | MLP | 1,910 / 8,087 / 1,351 | 36,037 / 163,609 / 35,830 | 2 |
| Racine | Linear | 1,258 / 2,073 / 0,410 | 9,394 / 31,967 / 8,569 | 16 |
| Racine | Smooth | 1,383 / 3,067 / 0,664 | 12,961 / 32,398 / 12,278 | 7 |
| Dernier joint | Référence | 1,371 / 2,552 / — | 5,014 / 7,121 / — | 8 |
| Dernier joint | MLP | 2,084 / 8,821 / 1,540 | 44,603 / 183,378 / 44,053 | 2 |
| Dernier joint | Linear | 1,282 / 2,265 / 0,377 | 8,590 / 29,304 / 7,586 | 16 |
| Dernier joint | Smooth | 1,404 / 3,002 / 0,658 | 12,913 / 33,638 / 12,469 | 2 |
| Angles locaux | Référence | 1,823 / 3,217 / — | 5,946 / 9,339 / — | 10 |
| Angles locaux | MLP | 2,385 / 9,050 / 1,571 | 47,197 / 179,359 / 46,988 | 2 |
| Angles locaux | Linear | 1,718 / 2,824 / 0,512 | 11,944 / 40,563 / 10,871 | 16 |
| Angles locaux | Smooth | 1,886 / 4,177 / 0,881 | 17,606 / 43,641 / 16,779 | 7 |

L’erreur de position RMS au dernier joint est de **4,70 cm** (Linear), **5,45 cm** (Smooth) et **10,40 cm** (MLP). Le pic MLP à l’image 2 est calculé sur `1 → 2 → 3`, donc juste après l’ancre initiale.

## Raccords aux ancres

Au dernier joint, vitesses unilatérales en m/s. Aux ancres intérieures, la valeur entre parenthèses est la norme de la **différence vectorielle** des vitesses divisée par l’écart entre les milieux des deux intervalles (accélération discrète, m/s²). La sortie JSON donne aussi `‖Δv‖` non divisé, ainsi que le détail racine et angles.

| Variante | Départ `1 → 2` | `7 → 7,5 → 8` : avant / après (accél.) | `15 → 16 → 17` : avant / après (accél.) | Arrivée `24 → 25` |
|---|---:|---:|---:|---:|
| Référence | 2,552 | 1,375 / 1,256 (7,097) | 0,903 / 0,943 (3,948) | 1,189 |
| MLP | 8,821 | 1,468 / 2,551 (191,327) | 2,086 / 1,424 (83,180) | 2,556 |
| Linear | 2,265 | 1,790 / 0,698 (76,978) | 0,629 / 0,990 (29,304) | 1,023 |
| Smooth | 0,948 | 0,388 / 0,119 (16,832) | 0,204 / 0,305 (9,121) | 0,316 |

Le MLP atteint **191,33 m/s²** à 7,5 au dernier joint, un point absent de la grille uniforme. À 16, ses deux vitesses de racine ont presque la même norme (0,695 et 0,696 m/s), mais leur différence vectorielle vaut **1,273 m/s** : le changement de direction compte. Smooth réduit les vitesses près des ancres, au prix d’un ralentissement absent de la référence ; un raccord moins brusque n’implique pas une meilleure fidélité temporelle.

## Portée de la mesure et droits

Le banc calcule des **différences finies sur les poses canoniques**, pas des dérivées instantanées ni une preuve de continuité mathématique. Il ne réévalue ni les F-curves (poignées, interpolation des composantes du quaternion entre clés), ni les matrices évaluées par le depsgraph Blender, ni le skin. La preuve Blender existante contrôle les poses aux clés et la FK sur cette chaîne ; elle ne valide pas les dérivées de la lecture entre clés. Smooth est une baseline supplémentaire, pas une Action livrée dans la planche. Une mesure Blender complémentaire devrait échantillonner les têtes des `DEF` évaluées aux mêmes temps, puis plus densément autour des ancres pour isoler l’effet des courbes, sans confondre cette lecture avec la cuisson GLB à 48 Hz.

Ce banc ne permet aucune conclusion sur Rigify, les humanoïdes, le glissement des pieds ni la prise d’arme. Les poses verrouillées et les contacts d’accessoires sont des invariants à vérifier sur l’animation évaluée, pas des promesses du modèle. Ici, il n’y a ni accessoire ni contact ; le worker refuse les requêtes de contact.

Le [code original](../LICENSE) relève de GPL-3.0-or-later ; le **checkpoint original** est séparément distribué sous GPL-3.0-or-later avec ses sources d’entraînement. La **trajectoire et la géométrie** de la démo sont des créations synthétiques originales sous la même licence, sans corpus ou capture tierce. Ces droits ne s’étendent pas à d’autres modèles ou données ; voir le [dossier modèles et licences](sprint-4-modeles.md). NumPy reste une dépendance externe.

## Reproduction et vérification

Depuis la racine, utiliser le Python d’un **environnement dédié à ce modèle** avec NumPy déjà disponible. Rien à installer dans Blender, aucun réentraînement ni téléchargement ; le JSON de mesures est émis sur stdout et les fichiers de travail sont supprimés.

```bash
TMPDIR=/tmp/opencode OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \
  python3 -B backends/inbetweening/sprint4_continuity.py
```

Sur un autre hôte, omettre `TMPDIR` ou choisir un répertoire temporaire existant. `--proof` et `--weights` acceptent des chemins locaux vers les mêmes artefacts.

Exécuté sous Linux x86-64, CPython 3.14.10 / NumPy 2.5.5. `build_extension.sh` puis `test_headless.sh` ont également réussi sur une copie temporaire, avec Blender 5.2.1 LTS et `SAS_MOTION_TEST_PYTHON` activé (worker réel, ancres exactes, FK, skin et réimport GLB temporaire). Avertissement sans échec au retrait : dépôt optionnel `blender_org` absent ; désinstallation confirmée. Pas de nouvelle recette Godot, Windows ou interactive, ni de mesure de dérivées dans Blender.
