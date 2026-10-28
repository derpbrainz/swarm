// Shared catalog consumers, with no name-based or guessed balance defaults.
export function canFireWhileRunning(player, items) {
  if (player?.capabilities) return player.capabilities.can_fire_while_running;
  const inv = player?.inventory;
  const item = inv?.equipped?.[inv.active_weapon_slot];
  return Boolean(item && items[item.template_id]?.weapon.can_fire_while_running);
}

export function playerSightRange(player, classes, items) {
  if (Number.isFinite(player.sight_range)) return player.sight_range;
  const cls = classes[player.class_id];
  if (!cls) return null;
  const inv = player.inventory;
  const carried = [...(inv?.storage || []), inv?.equipped?.quick_use].filter(Boolean);
  const boost = Math.max(0, ...carried.map(item => {
    const effect = items[item.template_id]?.effect;
    if (effect?.behavior !== 'sight_boost') return 0;
    return item.value?.sight_boost ?? effect.sight_boost;
  }));
  return cls.sight_range + boost;
}

export function audioAssets(catalog) {
  return {
    ...Object.fromEntries(Object.entries(catalog.sounds).map(([id, sound]) => [id, sound.asset])),
    ...Object.fromEntries(Object.entries(catalog.music.tracks).map(([state, path]) => [`music_${state}`, path])),
  };
}
