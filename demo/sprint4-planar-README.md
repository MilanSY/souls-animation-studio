# Sprint 4 — Entre les poses

Ouvrir [le `.blend`](sprint4-planar.blend) sans activer les scripts automatiques, puis lancer la lecture de la timeline : **IA (MLP)**, **CPU (Linear SAS)** et **référence synthétique** avancent côte à côte à la même cadence. Le fichier s’ouvre à l’image 2, celle qui sépare le plus les positions du dernier joint MLP et CPU parmi les images libres. La [planche PNG](sprint4-planar.png) en montre un arrêt sur image. Les tracés pointillés sont ceux de la racine et du dernier joint de la **référence**, dans les trois panneaux ; les points plus gros marquent les ancres. La réglette et le numéro d’image de la planche restent fixes pendant la lecture. Le marqueur Blender « Ancre 7.5 » est placé à 7 (les marqueurs sont entiers), mais **les clés sont bien à 7,5**.

Chaque collection contient un module, un rig CTRL et un squelette DEF séparé piloté par `Copy Transforms`. Les armatures sont masquées dans la vue par défaut ; les rendre visibles dans l’Outliner pour inspecter les os. L’Action active `MLP | ACTION - clip compare` est une copie complétée ; `MLP | SOURCE - 4 ancres` reste distincte, conservée dans le fichier par utilisateur fictif. Même séparation pour CPU et référence.

## Comparaison contrôlée

Un clip synthétique inédit de 24 intervalles à 24 i/s, quatre ancres communes `[1, 7.5, 16, 25]`, 22 images entières libres. Le vrai worker MLP tourne dans un processus Python/NumPy séparé. `CPUBackend` produit Linear (translation linéaire, rotations SLERP) à partir de la même requête ; la référence provient directement des équations synthétiques, sans passer par le worker. Les trois utilisent la même conversion de canaux locaux, la même géométrie et les mêmes poids. La mesure porte sur les positions évaluées dans Blender, avant déplacement pour la planche.

| Variante | RMS racine | RMS dernier joint |
|---|---:|---:|
| IA / MLP | 9,10 cm | 10,40 cm |
| CPU / Linear | **5,13 cm** | **4,70 cm** |

**Sur ce clip, Linear est plus proche de la référence.** Ce n’est pas un classement général. Les quatre poses ancrées conservent exactement leurs clés et leurs matrices CTRL évaluées. Le retarget explicite `root / hinge / tip` vers `Base CTRL / Pivot CTRL / Bout CTRL` a été comparé à une composition FK indépendante. Sur les trois peaux (756 sommets chacune, dont 96 à deux influences), le résultat Blender s’écarte de moins de 0,28 µm du skinning linéaire calculé indépendamment ; remplacer les poids mixtes par leur seul os dominant déplace jusqu’à 20,0 mm un sommet sur ce banc. Ce dernier écart montre que le mélange est effectif, pas qu’il est anatomiquement meilleur.

## GLB validé

[Le GLB](sprint4-planar.glb) contient **uniquement** la variante MLP cuite : un maillage skinné, trois os `DEF-*` et un clip d’une seconde. L’exporteur du dépôt reçoit une copie DEF liée directement au maillage, sans contrôles ni contraintes ; ni planche, ni variantes CPU/référence, ni Action source ne sont exportées. Pour garder l’ancre fractionnaire, la cuisson à **48 Hz** échantillonne les 49 demi-images de 1 à 25 incluses ; le modèle n’infère pas de poses supplémentaires à 48 Hz. Le GLB réimporté dans Blender a été comparé dans les deux sens par plus proche sommet déformé sur ces 49 poses : écart maximal **0,80 µm**.

Godot **4.7.2 stable** : import de ce même fichier par l’éditeur headless dans un projet temporaire indépendant, instanciation du `PackedScene` et lecture des 49 poses par `AnimationPlayer`. La peau est liée à la bonne chaîne (3 os et 3 binds) et l’écart maximal des têtes d’os est **0,63 µm** ; l’écart maximal de l’axe Y unitaire des os est inférieur à `6,3e-7`. Pour reproduire cette fidélité, configurer l’import Godot à **48 fps**, sans trimming, sans retrait des pistes immuables et avec **Optimizer désactivé** sur `AnimationPlayer`, comme dans le générateur. Un essai sans désactiver l’optimiseur dépassait la tolérance de 0,2 mm. La sonde Godot vérifie la structure et les poses, pas les sommets déformés par le GPU ni un rendu dans le jeu.

## Reproduction hors ligne

Depuis la racine du dépôt, avec Python/NumPy déjà disponibles, Blender avec Cycles et glTF, et Godot sur le `PATH`. Environnement exécuté : Linux x86-64, CPython 3.14.10 / NumPy 2.5.5, Blender 5.2.1 LTS (`9e2066aef7ef`), Godot 4.7.2 stable. Aucun téléchargement ni répertoire de jeu requis.

```bash
out=$(mktemp -d /tmp/opencode/sas-sprint4-repro.XXXXXXXX)
python3 -B scripts/build_sprint4_demo.py --output "$out"
```

`--scratch-parent` permet de choisir un autre répertoire temporaire existant, `--blender` et `--godot` des exécutables précis. Le script réentraîne le checkpoint original depuis zéro (seed `4072026`, un thread BLAS/OMP demandé), génère le clip (seed `4403`), lance le worker sur la requête produite par Blender, puis rend, rouvre et contrôle les fichiers. Blender et Godot sont lancés avec des profils isolés ; le projet Godot et les intermédiaires sont supprimés à la fin. Les fichiers ne sont copiés dans la destination qu’après les validations ; la commande refuse d’écraser une sortie précédente. Pour examiner la preuve, voir [le JSON](sprint4-planar-preuve.json) : versions, empreintes des fichiers et scripts, requête/réponse v1, référence, mesures et invariants. Comparer les mesures et les empreintes des poids ; l’identifiant de requête est neuf à chaque exécution, et l’identité binaire des sorties entre exécutions ou entre piles n’est pas requise.

`build_extension.sh` et `test_headless.sh` ont également réussi sur une copie temporaire du dépôt, sans changer les scripts ni les tests. Le retrait de l’extension a été confirmé malgré un avertissement `blender_org` absent. La suite commune a exercé Rigify ; **la démo Sprint 4 n’en dépend pas**. Le test ciblé existant `headless_ai_retarget.py` a aussi réussi dans son mode synthétique. Pas de recette interactive ni sous Windows ; les tests Godot optionnels de la suite commune n’ont pas été lancés (la sonde Godot de cette démo a été exécutée).

Le [banc Rigify Sprint 3](sprint3-skinning-README.md) compare déjà Bone Heat, poids lissés et Preserve Volume sur des mannequins A/T. Ici, le contrôle indépendant du skinning linéaire est volontairement limité aux modules synthétiques : l’adaptateur MLP exige trois os FK directs orientés le long de +X au repos et rejette Rigify. Aucun résultat du banc humanoïde n’est attribué au MLP.

## Périmètre et licence

Preuve expérimentale d’une **chaîne FK planaire à trois os** et d’un horizon fixe. Ni humanoïde, ni retarget Rigify/généraliste, ni arme, ni résolution ou validation de contacts. Le module, les poses et le [checkpoint `.f32`](sprint4-planar.f32) sont originaux et livrés sous [GPL-3.0-or-later](../LICENSE) ; aucune capture, donnée, géométrie ou poids tiers. Les sources d’entraînement et d’inférence sont dans `backends/inbetweening/sprint4_train.py` et `sprint4_worker.py`. NumPy est une dépendance d’exécution externe, non redistribuée. Le checkpoint n’est ni intégré au `.blend` ni chargé à son ouverture.
