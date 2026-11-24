# Guide — Souls Animation Studio 0.2.0

## Quick Rigging

1. Sélectionner un **maillage en mode Objet**. Le personnage doit être debout en **A-pose descendante**, raisonnablement symétrique, pieds séparés, avec **+Z vers le haut et −Y vers l’avant dans le repère local**. Utiliser un seul maillage éditable à utilisateur unique, sans armature existante. Les vêtements séparés, doigts articulés, les poses en T ou très asymétriques ne sont pas pris en charge par l’estimateur. Sur une copie, appliquer une échelle non uniforme si elle décale la pose IK neutre.
2. Pour un aperçu rapide, lancer **Quick Rig All-in-One**. Pour une production, préférer les étapes manuelles : **Create Meta-rig**, **Fit to Mesh**, puis **Adjust in Edit Mode**. Vérifier en vues avant et de côté que hanches, genoux, chevilles, épaules et coudes sont centrés dans la surface. Le placement vient de proportions et de sections du maillage, jamais d’une détection anatomique par IA. Si l’estimation est refusée, on peut ajuster le gabarit à la main.
3. **Finish Adjustment → Generate Control Rig → Bind with Automatic Weights → Validate Rig**. Le skinning ajoute un modificateur Armature et des groupes DEF sans changer les coordonnées, le parent ou les transformations du maillage. Il refuse d’écraser un binding ou des groupes homonymes. Vérifier les messages d’erreur et corriger topologie/alignement avant de relancer. Après génération, ne pas écraser le rig existant pour changer le placement : annuler ou repartir du maillage non lié avec un nouveau métarig.

Le maillage actif prime sur le champ Mesh. Vérifier les trois cibles affichées lorsqu’il y a plusieurs personnages. Les opérations d’édition et de rigging participent à l’Undo Blender. L’export écrit un fichier et ne peut pas être annulé par Undo.

## Contrôleurs

- Sélectionner le **Control Rig** et passer en mode Pose. `torso` déplace le corps, `hips` oriente le bassin, `chest` porte la colonne, `head` oriente la tête.
- `hand_ik.L/R` et `foot_ik.L/R` pilotent les extrémités en IK. `upper_arm_fk`, `forearm_fk`, `hand_fk` et `thigh_fk`, `shin_fk`, `foot_fk` sont les chaînes FK, avec suffixes `.L`/`.R`.
- Régler `IK_FK` sur `upper_arm_parent.L/R` et `thigh_parent.L/R` : **0 = IK, 1 = FK**. Afficher au besoin les collections d’os IK/FK voulues. Changer le mode au milieu d’un plan ne cale pas automatiquement IK et FK.
- Les os `DEF-` sont les os déformants. Les widgets des contrôleurs ne font pas partie du maillage et ne sont pas exportés.
- **Reset Pose** réinitialise les contrôleurs et leurs propriétés numériques par défaut. Il ne supprime aucune clé. Sur une action active, une nouvelle évaluation de l’animation peut réappliquer les clés : détacher temporairement l’action pour contrôler le repos sans animation.

La démonstration est un mannequin sans doigts séparés. Elle n’embarque pas le texte Python du panneau généré de Rigify ; les contrôleurs, leurs contraintes et les propriétés d’os restent accessibles sans Auto Run Python Scripts. Pour retrouver l’interface propre à Rigify, générer un nouveau rig depuis un métarig avec le Rigify local.

## CPU Inbetweening

Créer ou sélectionner une **Action** active sur le Control Rig, avec au moins deux poses clés séparées par une image intermédiaire entière. Définir **Start / End**, puis **Linear** (progression uniforme) ou **Smooth** (accélération et décélération à chaque segment). Par défaut, toutes les clés de la plage sont prises en compte. On peut cocher **Use selected keyframes** pour prendre la sélection du Dope Sheet/Éditeur d’actions, ou entrer une liste dans **Keyframes**, qui a priorité. Les images choisies bornent l’intervalle ; toutes les clés originales comprises dans cet intervalle restent protégées.

**Generate Inbetweens** crée une Action distincte. La sélectionner dans l’**Éditeur d’actions** pour l’essayer ; la source reste active tant qu’on ne change pas d’action. Les rotations de pose compatibles sont interpolées dans l’espace quaternion sur l’arc court, même lorsque leur stockage est Euler. L’interpolation est échantillonnée aux images entières ; les sous-images intermédiaires restent soumises aux courbes Blender. Les canaux discrets, verrouillés, muets ou pilotés ne sont pas réécrits. L’outil ne résout pas les collisions, n’impose pas d’appui au sol et ne traite pas la pile NLA.

## GLB expérimental

1. Vérifier le binding. Sélectionner le clip voulu comme action active et régler la plage inclusive **Start / End**. Pour exporter la pose courante sur un seul échantillon, donner deux bornes égales à son image.
2. Dans **GLB Export → Export GLB**, choisir un fichier `.glb`. L’extension échantillonne les os de déformation **après** l’évaluation des contrôles, chaque image, et écrit un squelette de déformation séparé.
3. Importer le GLB dans un **nouveau projet Godot**. Ne pas pointer vers le projet de jeu.

Un maillage, un modificateur Armature, skinning linéaire, huit poids par sommet au plus, pas de morphs ni de modificateurs géométriques actifs supplémentaires. Le Quick Rig crée des DEF avec un seul segment B-Bone. Animer le contrôle `root`, pas les transformations de l’objet armature. glTF décrit chaque articulation par translation/rotation/échelle et ne peut pas reproduire exactement un cisaillement : si l’export le refuse, réduire **IK Stretch** ou les échelles extrêmes. La conversion standard Blender glTF est **Z-up → Y-up** ; pour le mannequin, l’avant **−Y Blender → +Z glTF/Godot**. Si le jeu utilise une autre convention, adapter le nœud parent dans son pipeline plutôt que de modifier le mannequin.
