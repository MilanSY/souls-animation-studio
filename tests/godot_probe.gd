# SPDX-License-Identifier: GPL-3.0-or-later
extends SceneTree

const EPSILON := 0.02 # meters, to allow importer floating-point differences


func _initialize() -> void:
	call_deferred("_run")


func fail(message: String) -> void:
	printerr("GLB import probe: ", message)
	quit(1)


func _run() -> void:
	var args := OS.get_cmdline_user_args()
	if args.size() < 1 or args.size() > 2:
		fail("expected a GLB path and optional JSON reference")
		return

	var reference: Dictionary = {}
	if args.size() == 2:
		var parsed: Variant = JSON.parse_string(FileAccess.get_file_as_string(args[1]))
		if not parsed is Dictionary:
			fail("the JSON reference is missing or malformed: " + args[1])
			return
		reference = parsed
		if not valid_reference(reference):
			fail("the JSON reference does not contain valid bounds, duration and joint information")
			return
	var bone_name: String = reference.get("bone", "DEF-upper_arm.L")
	var document := GLTFDocument.new()
	var state := GLTFState.new()
	var status := document.append_from_file(args[0], state)
	if status != OK:
		fail("GLTFDocument.append_from_file returned " + error_string(status))
		return
	var imported := document.generate_scene(state)
	if imported == null:
		fail("GLTFDocument.generate_scene did not generate a scene")
		return
	root.add_child(imported)

	var meshes := imported.find_children("*", "MeshInstance3D", true, false)
	var skeletons := imported.find_children("*", "Skeleton3D", true, false)
	var players := imported.find_children("*", "AnimationPlayer", true, false)
	if meshes.size() != 1 or skeletons.size() != 1 or players.is_empty():
		fail("expected one character mesh, one skeleton and at least one AnimationPlayer")
		return
	var mesh_instance: MeshInstance3D = meshes[0]
	var skeleton: Skeleton3D = skeletons[0]
	var skin := mesh_instance.skin
	var target_index := skeleton.find_bone(bone_name)
	if skin == null or mesh_instance.get_node_or_null(mesh_instance.skeleton) != skeleton:
		fail("imported mesh has no skin referencing the imported skeleton")
		return
	if target_index < 0 or skeleton.get_bone_count() <= 10:
		fail("expected a humanoid skeleton with the deform bone " + bone_name)
		return
	var skin_has_arm := false
	if skin.get_bind_count() != int(reference.get("joint_count", skin.get_bind_count())):
		fail("Godot did not preserve the glTF joint bind count")
		return
	for i in range(skin.get_bind_count()):
		var bind_name := skin.get_bind_name(i)
		var bone := skeleton.find_bone(bind_name) if bind_name else skin.get_bind_bone(i)
		if bone < 0 or bone >= skeleton.get_bone_count() or not skin.get_bind_pose(i).is_finite():
			fail("invalid imported skin bind #" + str(i))
			return
		skin_has_arm = skin_has_arm or bone == target_index
	if not skin_has_arm:
		fail("the deform arm exists but is not bound to the skin")
		return
	if mesh_instance.mesh == null or not valid_surface_weights(mesh_instance.mesh, skin.get_bind_count()):
		fail("imported mesh has missing or invalid bone indices/vertex weights")
		return

	var box := mesh_instance.global_transform * mesh_instance.get_aabb()
	if not reference.is_empty():
		var lower: Array = reference["aabb_min"]
		var upper: Array = reference["aabb_max"]
		var expected_min := Vector3(lower[0], lower[1], lower[2])
		var expected_max := Vector3(upper[0], upper[1], upper[2])
		if box.position.distance_to(expected_min) > EPSILON or box.end.distance_to(expected_max) > EPSILON:
			fail("axis/origin/scale mismatch: actual %s, expected %s to %s" % [box, expected_min, expected_max])
			return
	else:
		# Standalone mode assumes the upright, meter-scale SAS reference mannequin.
		if not (1.5 < box.size.y and box.size.y < 2.1 and box.size.z < box.size.x and box.size.x < box.size.y):
			fail("unexpected axis or scale for the upright reference humanoid: " + str(box))
			return

	for player_node in players:
		var player: AnimationPlayer = player_node
		for clip in player.get_animation_list():
			var animation := player.get_animation(clip)
			if animation.length <= 0.0:
				continue
			if reference.has("duration") and absf(animation.length - float(reference["duration"])) > 0.01:
				continue
			for track in range(animation.get_track_count()):
				if is_varying_arm_track(animation, track, bone_name):
					player.play(clip)
					player.seek(0.0, true)
					var pose_start := skeleton.get_bone_pose_rotation(target_index)
					player.seek(animation.length * 0.9, true)
					var pose_end := skeleton.get_bone_pose_rotation(target_index)
					if absf(pose_start.dot(pose_end)) >= 0.999:
						fail("the varying rotation track did not move the imported deform bone")
						return
					print("Godot %s: %d bones, %d binds, AABB %s; %s (%ss) moves %s" % [
						Engine.get_version_info().string, skeleton.get_bone_count(),
						skin.get_bind_count(), box, clip, animation.length, bone_name,
					])
					quit(0)
					return
	fail("no imported animation contains a nonconstant rotation channel for " + bone_name)


func valid_reference(reference: Dictionary) -> bool:
	if not reference.get("bone") is String or not reference.get("duration") is float \
			or not reference.get("joint_count") is float:
		return false
	for key in ["aabb_min", "aabb_max"]:
		var value: Variant = reference.get(key)
		if not value is Array or value.size() != 3:
			return false
		for component in value:
			if not component is float or not is_finite(component):
				return false
	return true


func valid_surface_weights(mesh: Mesh, bind_count: int) -> bool:
	if mesh.get_surface_count() < 1:
		return false
	for i in range(mesh.get_surface_count()):
		var arrays := mesh.surface_get_arrays(i)
		if arrays[Mesh.ARRAY_BONES] == null or arrays[Mesh.ARRAY_WEIGHTS] == null:
			return false
		var vertices: PackedVector3Array = arrays[Mesh.ARRAY_VERTEX]
		var bones: PackedInt32Array = arrays[Mesh.ARRAY_BONES]
		var weights: PackedFloat32Array = arrays[Mesh.ARRAY_WEIGHTS]
		var count := vertices.size()
		var influence_count := 8 if bones.size() == 8 * count else 4
		if count == 0 or bones.size() != influence_count * count or weights.size() != bones.size():
			return false
		for vertex in range(count):
			var total := 0.0
			for slot in range(influence_count):
				var item := vertex * influence_count + slot
				var weight := weights[item]
				if not is_finite(weight) or weight < 0.0 or weight > 1.0:
					return false
				if weight > 0.0 and (bones[item] < 0 or bones[item] >= bind_count):
					return false
				total += weight
			if absf(total - 1.0) > 0.002:
				return false
	return true


func is_varying_arm_track(animation: Animation, track: int, bone_name: String) -> bool:
	if animation.track_get_type(track) != Animation.TYPE_ROTATION_3D:
		return false
	if not animation.track_is_enabled(track):
		return false
	var path := animation.track_get_path(track)
	if path.get_subname_count() != 1 or path.get_subname(0) != bone_name:
		return false
	var count := animation.track_get_key_count(track)
	if count < 2:
		return false
	var first: Quaternion = animation.track_get_key_value(track, 0)
	for key in range(1, count):
		var next: Quaternion = animation.track_get_key_value(track, key)
		if absf(first.dot(next)) < 0.999:
			return true
	return false
