# Référence CPU d’inbetweening et futurs adaptateurs

La référence CPU n’est pas un modèle IA. Elle ne télécharge rien et ne requiert ni poids, ni corpus, ni dépendance Python externe. Le code du dépôt est GPL-3.0-or-later. Les fixtures sont générées localement et ne redistribuent ni Rigify ni données tierces.

## Comportement Blender

- `addon.animation.keyframes_in_range(rig, start, end)` lit l’union triée des temps de clés du slot actif, bornes incluses, sous-images comprises. Les courbes muettes sont exclues. `selected_keyframes_in_range` ne retient que les temps avec au moins un point de clé sélectionné (une poignée seule ne compte pas).
- `generate_inbetweens(context, rig, start, end, mode, keyframes=None)` accepte `Linear` ou `Smooth` (sans distinction de casse), et par défaut toutes les clés de la plage. Une sélection explicite doit contenir au moins deux temps de clés distincts. Ses extrêmes bornent le segment régénéré ; toute clé originale intermédiaire, même non sélectionnée, verrouille aussi sa pose. Aucune extrapolation n’est générée aux bords de la plage. Une plage sans image entière libre est refusée.
- Les canaux continus éditables des pose bones sont échantillonnés entre ancres à chaque image entière libre. `Linear` utilise `t`, `Smooth` utilise `t²(3−2t)`. Les rotations quaternion et Euler actives utilisent la SLERP de l’arc court (conversion Euler dans Blender). Les rotations axe-angle, canaux discrets (dont les contrôleurs bool/int), courbes verrouillées, pilotes, canaux d’objet et autres slots ne sont pas régénérés. Le calcul ne résout pas les contacts pieds-sol ou main-accessoire et ne doit pas être présenté comme conservant ces contacts entre ancres.
- Le résultat est une copie nommée de l’action, conservée par un utilisateur fictif. Il n’active pas la copie et ne change ni l’action source ni l’image courante. L’action source, son slot et ses clés ne sont pas modifiés. Les coordonnées des clés originales sont conservées dans la copie et l’égalité exacte des valeurs de courbes est vérifiée à chaque ancre traitée. La copie n’est toutefois pas identique entre clés : ses segments interpolés et leurs poignées changent et des raccords proches des bornes peuvent changer hors intervalle si les courbes sont clairsemées. Entre les images échantillonnées, Blender utilise ses F-Curves ; il ne réévalue pas la SLERP, notamment lors des sauts de représentation Euler ou de signe quaternion.
- Le calcul vise l’action active, pas la pile NLA, et refuse le mode tweak NLA, les actions avec plusieurs strips remplis pour le slot actif, ainsi que les courbes à régénérer avec modificateurs ou échantillons natifs. Les erreurs de demande sont des `ValueError`, l’absence ou l’incompatibilité des données Blender sont signalées par `RuntimeError`.

## Contacts (mode explicite)

`generate_contact_inbetweens(context, rig, start, end, mode, keyframes=None)` produit une
copie CPU et demande à Blender de résoudre les contacts natifs déjà persistés sur le rig.
L’évaluateur assigne temporairement la copie au rig réel afin de conserver les cibles,
pilotes et dépendances de la scène. Il restaure l’action source, son slot et l’image
courante (sous-image comprise), y compris en cas d’erreur. Il compare à l’identique les
matrices de la pose source et de la copie aux ancres, avec le même rig et les mêmes
contraintes ; une différence rejette et supprime la copie.

Le résultat contient `.action`, `.residuals` et `.violations`. Chaque mesure donne
`.frame`, `.locked`, `.limb`, `.distance` (unités Blender en espace monde ou `None`),
`.orientation_error` (radians ou `None`), `.influence`, `.ik`, `.satisfied`
et `.message`. Les contacts
d’influence non nulle sont mesurés sur la déformation effective à chaque image
entière générée et à chaque ancre, y compris les sous-images. Un écart supérieur à
0,001 unité Blender ou 1° est signalé, même pendant une transition d’influence.
Un contact invalide est également signalé, même sans influence mesurable.
Une violation ne modifie pas les ancres, ne déplace pas les accessoires et ne
déclenche pas de réécriture des canaux. Une plage sans aucun contact actif donne
un rapport vide, pas une preuve de contact. Les autres sous-images ne sont pas
contrôlées.

La copie seule ne contient pas la solution IK : il faut la rejouer sur le rig
avec ses contraintes et cibles présentes. Pour rendre le mouvement autonome
en GLB, l’export doit échantillonner la pose évaluée (pas les canaux des
contrôleurs bruts).

## Interface locale

`addon/animation/cpu.py` définit `Backend.generate(Request) -> tuple[Pose, ...]` sans import Blender. Les ancres contiennent des valeurs scalaires et des rotations WXYZ, ordonnées par l’adaptateur Blender. Le moteur retourne seulement des propositions aux images entières non verrouillées ; l’adaptateur garde la maîtrise des ancres, de la conversion des canaux et de l’écriture. Ce contrat Python n’est pas encore un format d’échange externe.

Pour un futur modèle, prévoir un processus CLI local hors Blender, avec l’environnement et les versions épinglées propres à ce modèle. Utiliser une demande/réponse versionnée, des fichiers non exécutables et un dossier de travail isolé. Absence de réseau et de chemin vers le projet Godot pendant le traitement. Vérifier séparément licence du code, conditions des poids et droits sur les données avant utilisation ou redistribution. Les poses verrouillées et contacts annotés devront être vérifiés sur le rig en aval ; un conflit non résolu doit bloquer la validation plutôt qu’être présenté comme une garantie du modèle. Voir [l’architecture](../../docs/architecture.md).

## Tests ciblés

```sh
PYTHONDONTWRITEBYTECODE=1 python3 backends/inbetweening/test_cpu.py
PYTHONDONTWRITEBYTECODE=1 blender --background --factory-startup --offline-mode \
  --python-exit-code 1 --python backends/inbetweening/test_blender.py
```

Le second test nécessite que le module Rigify soit disponible localement dans Blender ; il génère un rig réel, utilise deux slots et mesure les courbes ainsi que les matrices de poses verrouillées via le graphe de dépendances.
