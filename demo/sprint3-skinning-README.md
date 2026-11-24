# Mission 1 — Banc de skinning Sprint 3

Ouvrir [`sprint3-skinning.blend`](sprint3-skinning.blend) sans autoriser les scripts automatiques. Les deux morphologies synthétiques (élancée en A, trapue en T) dérivent du mannequin lofté SAS, sans asset importé. Le fichier s’ouvre à l’image 9 avec les deux peaux linéaires Sprint 3 visibles. La timeline marque **1 repos, 9 élévation, 17 flexion, 25 torsion, 33 combiné bras/jambe**. Les commandes sont des rotations FK, pas des angles anatomiques mesurés. Pour chaque morphologie, les trois peaux sont superposées : masquer celle qui est visible et afficher la variante voulue dans l’Outliner (œil pour la vue, caméra pour le rendu). Les contrôleurs Rigify et le métarig ajusté sont masqués ; les 35 os DEF de chaque armature séparée suivent le rig de contrôle par contraintes Copy Transforms.

## Comparaison contrôlée

- **Avant — Bone Heat normalisé** : Blender Bone Heat sur les os DEF au repos, normalisation par sommet, toutes les influences positives conservées. Pas de lissage ni de limite. C’est une référence algorithmique, **pas** une exécution du pipeline Sprint 2 ; le placement Sprint 3 est commun à tous les essais.
- **Après — Sprint 3 linéaire** : binder de l’extension (`separate=True`), poids lissés, normalisés et plafonnés à quatre influences, déformation linéaire.
- **Variante — Sprint 3 Preserve Volume** : exactement les mêmes poids et poses que l’après, avec l’option de déformation dual-quaternion de Blender. Non exportée en GLB.

Même géométrie et mêmes matrices DEF pour les trois méthodes. Planches orthographiques, cadrage et éclairage identiques entre variantes : [silhouettes A](sprint3-skinning-01.png), [silhouettes T](sprint3-skinning-02.png), [gros plans épaule/coude](sprint3-skinning-03-details.png). Les maillages des planches sont des instantanés de la déformation évaluée ; le `.blend` conserve les peaux et les armatures animées.

## Résultats

Sur chaque fixture (22 800 sommets), le maximum d’influences passe de **7 à 4**, avec un écart maximal à la somme unitaire inférieur à `6e-8`. Le nombre de sommets dépassant quatre influences passe de **1 916 à 0** (A) et de **4 748 à 0** (T). Le saut quadratique moyen des poids par arête, en comparant les variantes telles quelles (budgets d’influences différents), diminue d’environ **1,8 %** en A mais augmente de **6,6 %** en T.

Les [mesures JSON](sprint3-skinning-mesures.json) suivent les **mêmes arêtes** dans toutes les poses : milieu de l’arête au repos à ≤ 115 mm du centre DEF de l’épaule gauche ou ≤ 85 mm du centre DEF du coude gauche. On rapporte Q05, médiane et Q95 du ratio *longueur posée / longueur au repos*, et les fractions d’arêtes dont le ratio est inférieur à 0,80 ou supérieur à 1,25. Exemples Q05–Q95 :

| Morphologie / pose / zone | Bone Heat normalisé | Sprint 3 linéaire | Sprint 3 Preserve Volume |
| --- | ---: | ---: | ---: |
| A / élévation / épaule | 0,38–1,46 | 0,38–1,48 | 0,42–1,56 |
| A / flexion / coude | 0,65–1,33 | 0,65–1,32 | 0,61–1,71 |
| T / élévation / épaule | 0,53–1,37 | 0,52–1,36 | 0,47–1,63 |
| T / flexion / coude | 0,61–1,35 | 0,60–1,36 | 0,58–1,83 |

**Le plafond d’influences est vérifié ; il n’y a pas de gain local systématique.** Dans ces flexions, Preserve Volume donne un coude visuellement plus rond, mais augmente Q95 d’étirement local. Des plis subsistent. Les ratios d’arêtes ne mesurent ni le volume local, ni les collisions, ni la fidélité anatomique. Deux variantes d’un même mannequin ne couvrent pas des topologies de production différentes.

## Reproduction et validation

Exécuté sous Linux avec Blender **5.2.1 LTS** (`9e2066aef7ef`), Python **3.13.13**, Rigify local **0.6.10**, Cycles **CPU** (16 échantillons, débruitage), et Godot **4.7.2 stable** en headless. Depuis la racine du dépôt :

```bash
scratch=$(mktemp -d /tmp/opencode/sas-skinning.XXXXXXXX)
cp -R addon scripts tests LICENSE "$scratch/"
unset BLENDER_USER_CONFIG BLENDER_USER_SCRIPTS BLENDER_USER_EXTENSIONS BLENDER_USER_DATAFILES
export BLENDER_USER_RESOURCES="$scratch/profile"
export PYTHONDONTWRITEBYTECODE=1

bash "$scratch/scripts/build_extension.sh"
bash "$scratch/scripts/test_headless.sh"
zip="$scratch/dist/souls_animation_studio-0.4.0.zip"
blender -b --factory-startup --offline-mode --command extension install-file --repo user_default --enable "$zip"
blender -b -t 4 --offline-mode --python-exit-code 1 --python "$scratch/tests/headless_rigging_sprint3.py"

blender -b -t 8 --factory-startup --offline-mode --disable-autoexec --python-exit-code 1 \
  --python "$scratch/scripts/build_sprint3_skinning_demo.py" -- \
  --output "$scratch/demo" --glb-probe-dir "$scratch/probes"

for id in 01 02; do
  bash "$scratch/tests/test_godot_import.sh" "$scratch/probes/sprint3-skinning-$id.glb"
done
blender -b -t 4 --factory-startup --offline-mode --disable-autoexec "$scratch/demo/sprint3-skinning.blend"
```

- Construction et suite ZIP : **18/18**, réouverture et désinstallation vérifiées ; suite ciblée placement/peau Sprint 3 : **3/3**.
- `.blend` rouvert en profil vierge sans auto-exécution : six peaux vivantes sur deux armatures DEF séparées, déformation vérifiée entre les images 1 et 9, ni bibliothèque liée ni texte exécutable.
- Les deux GLB temporaires importent chacun un maillage skinné, **35 os et 35 binds**, avec bornes cohérentes et un clip de **1,333 s** qui anime `DEF-upper_arm.L`. L’exporteur existant exige une liaison linéaire directe au rig de contrôle : le générateur vérifie sur les cinq poses un écart maximal séparé/direct inférieur à **2 µm** sur ces fixtures, exporte par ce chemin, puis restaure la liaison séparée. Ce test ne valide pas un export direct de l’armature séparée ni un aller-retour sommet par sommet dans Godot. Aucun test interactif de Blender ou du jeu ; aucun GLB ni ZIP temporaire livré.
- L’avertissement de dépréciation `Material.use_nodes` sous Blender 5.2 n’a pas bloqué la génération. La suite ZIP a aussi signalé un dossier `blender_org` absent lors de la désinstallation, puis confirmé la suppression réelle de l’extension.

Maillages et poses SAS sous GPL-3.0-or-later. Données de rig générées par Rigify 0.6.10, © Blender Foundation et contributeurs, GPL-2.0-or-later ([sources Blender](https://projects.blender.org/blender/blender)), réutilisées ici sous GPL-3.0-or-later ; pas de code Python Rigify embarqué.
