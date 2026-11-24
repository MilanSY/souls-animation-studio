# Éditer une pose et des contacts

## Smart Pose Editor

Dans la vue 3D, ouvrir la barre latérale (`N`), choisir le rig et passer en **Pose Mode**. Sélectionner les widgets Rigify directement dans la vue, ou utiliser les boutons de la section **Smart Pose Editor**. Les raccourcis Blender restent inchangés : `G` déplace, `R` tourne. Les boutons de coude et de genou sélectionnent le pôle IK, le contrôle de direction par rotation quand le pôle est désactivé, ou l’articulation FK suivant la configuration du membre. Ils ne changent pas le mode IK/FK. **Reset Selected Controls** remet à zéro seulement les transformations des contrôleurs sélectionnés, sans supprimer de clés.

## Contacts et prise d’objet

- Prendre une pose avec les pieds au sol, vérifier les membres concernés en IK et sélectionner le pied gauche ou droit. Laisser la cible vide pour le garder à sa position mondiale, ou choisir un objet/Empty pour conserver son décalage par rapport à cette cible. **Lock Orientation** verrouille aussi la rotation.
- La position capturée est celle du début de plage (ou la pose courante sans début), pas la projection sur une surface. La pose de capture doit donc déjà toucher le sol. Une seule plage inclusive par membre, sur une scène au remappage temporel 1:1. Vérifier les transitions aux limites. Retirer le contact rend à nouveau la main aux canaux de l’action ; la pause conserve son ancre. Reprendre une ancienne ancre éloignée peut faire sauter le membre, contrairement à une capture dans la pose courante.
- Pour un accessoire à une ou deux mains, placer d’abord l’objet et les mains sur la prise souhaitée. Dans **Weapon Setup**, choisir la main principale. L’objet suit cette main ; en mode deux mains, la main secondaire suit un décalage de prise de l’objet. Déplacer la main principale, pas l’objet contraint ; ne pas parenter l’objet au rig ou imposer une seconde chaîne d’attache concurrente.
- Un pied ou une main hors d’atteinte peut laisser le contrôleur à sa cible alors que l’os de peau n’y arrive pas. Utiliser le diagnostic de contact sur la pose courante et le rapport du CPU Inbetweening sur toute la plage. Corriger la portée, la position du bassin ou la trajectoire de l’objet plutôt que d’étirer systématiquement les membres.

## Bibliothèque et timeline

Nommer la pose, puis l’enregistrer sur le **Control Rig**. On peut dupliquer ou supprimer une entrée et appliquer une région (corps entier, haut, bas, côté ou sélection). **Mirror** échange les côtés par réflexion dans le repère du rig. Avec une seconde pose, le poids mélange la première vers la seconde ; sans seconde pose, il mélange la pose courante vers celle de la bibliothèque. Après application, insérer les canaux à l’image courante pour les inscrire dans l’action active. À cette étape, les clés qui occupent les mêmes canaux à cette image sont remplacées, comme lors d’une insertion Blender.

La bibliothèque est stockée dans le `.blend`. **Export / Import** échange un JSON local et versionné. L’import fusionne les noms compatibles et refuse les conflits plutôt que d’écraser une pose. Ce format vise les rigs SAS/Rigify compatibles : ce n’est ni du retargeting, ni une sauvegarde des réglages IK/FK, ni une copie des ancrages, de l’action ou des contraintes.

Le système d’assets natif de Blender prend des poses sous forme d’Actions marquées comme assets. Il est plus adapté aux miniatures et au navigateur d’assets ; le JSON de ce sprint privilégie l’échange léger et le contrôle explicite des canaux. Pas de synchronisation automatique avec la bibliothèque native. Pour une pose utilisable en asset natif, insérer une pose dans une action séparée et traiter cette action dans Blender.

## CPU Inbetweening avec contacts

Choisir une plage et des poses clés, puis lancer **Generate Contact-Aware Inbetweens**. Le backend effectue l’interpolation Linear ou Smooth avec SLERP ; Blender résout ensuite la pile IK et les ancrages du rig. Consulter le rapport **SAS Contact Report** dans le Text Editor : il mesure la distance et, lorsque l’orientation est verrouillée, l’erreur angulaire. Les seuils sont 0,001 unité Blender (1 mm sur le mannequin) et 1°. Une pose clé n’est jamais déplacée pour satisfaire un contact incompatible. Le texte répertorie jusqu’à 5 000 mesures, violations en premier.

Sélectionner l’Action créée pour la prévisualiser. Elle conserve ses contrôleurs animés et dépend des contacts du rig : la déplacer seule dans un autre fichier ne transporte pas leurs ancrages. L’export GLB cuit leur résultat sur les os DEF. Il n’inclut pas l’objet-épée ; le `.blend` conserve l’accessoire et ses attaches.
