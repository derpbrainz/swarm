/**
 * Isometric 2.5D HTML5 Canvas Renderer with Shared Co-op Fog of War,
 * 3D-extruded walls/doors, Spaceship drop bay, sight cones, and sound rings.
 */

import { TILE_W, TILE_H, WALL_HEIGHT_PX, worldToIso, computeVisibilityPolygon } from './iso.js';

export class IsometricRenderer {
  constructor(canvas, classesConfig = {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.classesConfig = classesConfig;
    this.exploredCells = new Set();
    this.lastMissionId = null;
    this.fogCanvas = document.createElement('canvas');
    this.fogCtx = this.fogCanvas.getContext('2d');
  }

  resetExplored() {
    this.exploredCells.clear();
  }

  setClassesConfig(cfg) {
    this.classesConfig = cfg || {};
  }

  render(state, localPlayerId, mouseWorld, camera) {
    const ctx = this.ctx;
    const W = this.canvas.width;
    const H = this.canvas.height;

    ctx.fillStyle = '#05080e';
    ctx.fillRect(0, 0, W, H);

    if (!state || !state.map) return;

    if (this.lastMissionId !== state.map.mission_id && state.mission_time < 1.0) {
      this.exploredCells.clear();
      this.lastMissionId = state.map.mission_id;
    }

    // 1. Compute active visibility polygons for all living players (Shared Team Vision)
    const visions = [];
    const walls = state.map.walls || [];
    for (const p of state.players || []) {
      if (p.hp <= 0) continue;
      const cls = this.classesConfig[p.class_id];
      const range = cls ? cls.sight_range : 12.5;
      const poly = computeVisibilityPolygon(p.x, p.y, range, walls, 84);
      visions.push({ player: p, range, poly });

      // Mark 2x2m floor cells within sight range & polygon bounding area as explored
      const minX = Math.max(0, Math.floor((p.x - range) / 2) * 2);
      const maxX = Math.min(state.map.width, Math.ceil((p.x + range) / 2) * 2);
      const minY = Math.max(0, Math.floor((p.y - range) / 2) * 2);
      const maxY = Math.min(state.map.height, Math.ceil((p.y + range) / 2) * 2);
      for (let gx = minX; gx < maxX; gx += 2) {
        for (let gy = minY; gy < maxY; gy += 2) {
          if (Math.hypot(gx + 1 - p.x, gy + 1 - p.y) <= range * 0.92) {
            this.exploredCells.add(`${gx},${gy}`);
          }
        }
      }
    }

    // 2. Draw floor segments & grid
    this.drawFloorSegments(ctx, state.map, camera);

    // 3. Draw Docked Spaceship & Extraction Zone
    if (state.map.spaceship) {
      this.drawSpaceship(ctx, state.map.spaceship, camera);
    }
    if (state.map.extraction_zone) {
      this.drawExtractionZone(ctx, state.map.extraction_zone, camera);
    }

    // 4. Draw Sound Rings & Deployables (Healing Fields, Sonar Pulses)
    this.drawSounds(ctx, state.sounds || [], camera);
    this.drawGroundDeployables(ctx, state.deployables || [], camera);

    // 5. Draw Puzzle Terminals
    const localPlayer = (state.players || []).find((p) => p.id === localPlayerId);
    this.drawPuzzles(ctx, state.map.puzzles || [], localPlayer, camera);

    // 6. Depth-sorted 2.5D entities (walls, doors, turrets, enemies, players)
    const renderables = [];

    for (const w of walls) {
      if (w.active === false) continue;
      renderables.push({
        depth: w.x + w.w * 0.5 + (w.y + w.h * 0.5),
        kind: 'wall',
        obj: w,
      });
    }

    for (const dep of state.deployables || []) {
      if (dep.type === 'turret') {
        renderables.push({
          depth: dep.x + dep.y,
          kind: 'turret',
          obj: dep,
        });
      }
    }

    for (const e of state.enemies || []) {
      if (!e.visible) continue;
      renderables.push({
        depth: e.x + e.y,
        kind: 'enemy',
        obj: e,
      });
    }

    for (const p of state.players || []) {
      renderables.push({
        depth: p.x + p.y,
        kind: 'player',
        obj: p,
      });
    }

    renderables.sort((a, b) => a.depth - b.depth);

    for (const item of renderables) {
      if (item.kind === 'wall') {
        this.drawIsoBox(ctx, item.obj, camera);
      } else if (item.kind === 'turret') {
        this.drawTurret(ctx, item.obj, camera);
      } else if (item.kind === 'enemy') {
        this.drawEnemy(ctx, item.obj, camera);
      } else if (item.kind === 'player') {
        this.drawPlayer(ctx, item.obj, item.obj.id === localPlayerId, mouseWorld, camera);
      }
    }

    // 7. Draw Projectiles
    this.drawProjectiles(ctx, state.projectiles || [], camera);

    // 8. Draw Shared Co-op Fog of War Overlay
    this.drawFogOfWar(ctx, state.map, visions, camera);

    // 9. Draw Crosshair at Mouse World Position
    if (mouseWorld && localPlayer && !localPlayer.downed) {
      const mIso = worldToIso(mouseWorld.x, mouseWorld.y, camera);
      ctx.save();
      ctx.strokeStyle = 'rgba(56, 189, 248, 0.85)';
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.arc(mIso.x, mIso.y, 9, 0, Math.PI * 2);
      ctx.moveTo(mIso.x - 14, mIso.y);
      ctx.lineTo(mIso.x + 14, mIso.y);
      ctx.moveTo(mIso.x, mIso.y - 14);
      ctx.lineTo(mIso.x, mIso.y + 14);
      ctx.stroke();
      ctx.restore();
    }
  }

  drawIsoQuad(ctx, x, y, w, h, camera, fillStyle, strokeStyle = null) {
    const p0 = worldToIso(x, y, camera);
    const p1 = worldToIso(x + w, y, camera);
    const p2 = worldToIso(x + w, y + h, camera);
    const p3 = worldToIso(x, y + h, camera);

    ctx.beginPath();
    ctx.moveTo(p0.x, p0.y);
    ctx.lineTo(p1.x, p1.y);
    ctx.lineTo(p2.x, p2.y);
    ctx.lineTo(p3.x, p3.y);
    ctx.closePath();
    if (fillStyle) {
      ctx.fillStyle = fillStyle;
      ctx.fill();
    }
    if (strokeStyle) {
      ctx.strokeStyle = strokeStyle;
      ctx.stroke();
    }
  }

  drawFloorSegments(ctx, map, camera) {
    const themeColors = {
      hangar: '#111927',
      corridor: '#131c2b',
      biolab: '#0f2224',
      reactor: '#1f1621',
      command: '#141e30',
      extraction: '#12241e',
    };

    for (const seg of map.segments || []) {
      const baseColor = themeColors[seg.theme] || '#121a28';
      this.drawIsoQuad(ctx, seg.x, seg.y, seg.w, seg.h, camera, baseColor, '#1e293b');

      // Subtle 2-unit isometric floor grid
      ctx.lineWidth = 1;
      for (let gx = seg.x; gx < seg.x + seg.w; gx += 2) {
        for (let gy = seg.y; gy < seg.y + seg.h; gy += 2) {
          const isExplored = this.exploredCells.has(`${gx},${gy}`);
          if (!isExplored) continue;
          this.drawIsoQuad(ctx, gx, gy, 2, 2, camera, null, 'rgba(148, 163, 184, 0.08)');
        }
      }

      // Segment title watermark on floor
      const centerIso = worldToIso(seg.x + seg.w * 0.5, seg.y + 1.8, camera);
      ctx.save();
      ctx.fillStyle = 'rgba(148, 163, 184, 0.35)';
      ctx.font = '600 12px Inter, system-ui, sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText(`SECTOR 0${seg.index + 1}: ${seg.name.toUpperCase()}`, centerIso.x, centerIso.y);
      ctx.restore();
    }
  }

  drawSpaceship(ctx, ship, camera) {
    // Dropship landing pad markings
    this.drawIsoQuad(
      ctx,
      ship.x - 0.4,
      ship.y - 0.4,
      ship.w + 0.8,
      ship.h + 0.8,
      camera,
      'rgba(245, 158, 11, 0.12)',
      'rgba(245, 158, 11, 0.45)'
    );
    // Ship interior deck
    this.drawIsoQuad(ctx, ship.x, ship.y, ship.w, ship.h, camera, '#1e293b', '#38bdf8');

    // Boarding ramp on East side
    const rampX = ship.x + ship.w;
    const rampY = ship.y + 0.7;
    const rampW = ship.ramp_open ? 2.0 : 0.4;
    const rampH = ship.h - 1.4;
    this.drawIsoQuad(
      ctx,
      rampX,
      rampY,
      rampW,
      rampH,
      camera,
      ship.ramp_open ? 'rgba(56, 189, 248, 0.28)' : 'rgba(239, 68, 68, 0.35)',
      ship.ramp_open ? '#38bdf8' : '#ef4444'
    );

    const labelIso = worldToIso(ship.x + ship.w * 0.5, ship.y + ship.h * 0.5, camera);
    ctx.save();
    ctx.fillStyle = '#38bdf8';
    ctx.font = 'bold 11px monospace';
    ctx.textAlign = 'center';
    ctx.fillText('DROPSHIP VALKYRIE-04', labelIso.x, labelIso.y - 6);
    ctx.fillStyle = ship.ramp_open ? '#4ade80' : '#facc15';
    ctx.fillText(ship.ramp_open ? '[RAMP DEPLOYED]' : '[PRESSURIZING RAMP...]', labelIso.x, labelIso.y + 10);
    ctx.restore();
  }

  drawExtractionZone(ctx, ext, camera) {
    const pulse = 0.5 + 0.5 * Math.sin(performance.now() * 0.005);
    const fill = ext.active
      ? `rgba(16, 185, 129, ${0.18 + pulse * 0.18})`
      : 'rgba(100, 116, 139, 0.12)';
    const stroke = ext.active ? '#10b981' : '#64748b';

    this.drawIsoQuad(ctx, ext.x, ext.y, ext.w, ext.h, camera, fill, stroke);

    const center = worldToIso(ext.x + ext.w * 0.5, ext.y + ext.h * 0.5, camera);
    ctx.save();
    ctx.textAlign = 'center';
    ctx.font = 'bold 12px monospace';
    ctx.fillStyle = ext.active ? '#34d399' : '#94a3b8';
    ctx.fillText(ext.active ? 'EXTRACTION BEACON ACTIVE' : 'EXTRACTION PAD (LOCKED)', center.x, center.y);
    if (ext.active) {
      ctx.font = '11px monospace';
      ctx.fillStyle = '#a7f3d0';
      ctx.fillText('ALL OPERATIVES GATHER HERE', center.x, center.y + 15);
    }
    ctx.restore();
  }

  drawPuzzles(ctx, puzzles, localPlayer, camera) {
    for (const pz of puzzles) {
      const iso = worldToIso(pz.x, pz.y, camera);
      const isSolved = pz.solved;
      const color = isSolved ? '#10b981' : '#f59e0b';

      // Interaction ring
      ctx.save();
      ctx.beginPath();
      ctx.ellipse(iso.x, iso.y, 26, 13, 0, 0, Math.PI * 2);
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.5;
      ctx.stroke();

      // Terminal pillar
      ctx.fillStyle = isSolved ? '#065f46' : '#78350f';
      ctx.fillRect(iso.x - 8, iso.y - 24, 16, 24);
      ctx.strokeStyle = color;
      ctx.strokeRect(iso.x - 8, iso.y - 24, 16, 24);

      ctx.font = 'bold 11px monospace';
      ctx.textAlign = 'center';
      ctx.fillStyle = color;
      ctx.fillText(isSolved ? 'ONLINE' : 'TERMINAL', iso.x, iso.y - 30);

      if (!isSolved && localPlayer && !localPlayer.downed) {
        const dist = Math.hypot(localPlayer.x - pz.x, localPlayer.y - pz.y);
        if (dist <= pz.radius) {
          ctx.fillStyle = '#38bdf8';
          ctx.font = 'bold 12px monospace';
          ctx.fillText('[PRESS E TO HACK]', iso.x, iso.y - 44);
        }
      }
      ctx.restore();
    }
  }

  drawSounds(ctx, sounds, camera) {
    for (const s of sounds) {
      const iso = worldToIso(s.x, s.y, camera);
      const alpha = Math.max(0, Math.min(1, s.ttl / 0.45)) * 0.45;
      const rx = s.radius * TILE_W * 0.35 * (1.1 - s.ttl);
      const ry = rx * 0.5;
      ctx.save();
      ctx.beginPath();
      ctx.ellipse(iso.x, iso.y, Math.max(4, rx), Math.max(2, ry), 0, 0, Math.PI * 2);
      ctx.strokeStyle =
        s.kind === 'alarm'
          ? `rgba(239, 68, 68, ${alpha})`
          : s.kind === 'gunfire'
          ? `rgba(251, 191, 36, ${alpha})`
          : `rgba(56, 189, 248, ${alpha * 0.7})`;
      ctx.lineWidth = 1.5;
      ctx.stroke();
      ctx.restore();
    }
  }

  drawGroundDeployables(ctx, deployables, camera) {
    for (const d of deployables) {
      const iso = worldToIso(d.x, d.y, camera);
      ctx.save();
      if (d.type === 'nano_field') {
        const rx = d.radius * (TILE_W * 0.5);
        const ry = d.radius * (TILE_H * 0.5);
        ctx.beginPath();
        ctx.ellipse(iso.x, iso.y, rx, ry, 0, 0, Math.PI * 2);
        ctx.fillStyle = 'rgba(34, 197, 94, 0.16)';
        ctx.fill();
        ctx.strokeStyle = '#22c55e';
        ctx.lineWidth = 2;
        ctx.stroke();
      } else if (d.type === 'sonar_pulse') {
        const rx = d.radius * (TILE_W * 0.45);
        const ry = rx * 0.5;
        ctx.beginPath();
        ctx.ellipse(iso.x, iso.y, rx, ry, 0, 0, Math.PI * 2);
        ctx.strokeStyle = 'rgba(168, 85, 247, 0.55)';
        ctx.lineWidth = 2;
        ctx.setLineDash([6, 4]);
        ctx.stroke();
      }
      ctx.restore();
    }
  }

  drawTurret(ctx, turret, camera) {
    const iso = worldToIso(turret.x, turret.y, camera);
    ctx.save();
    ctx.fillStyle = '#0284c7';
    ctx.beginPath();
    ctx.arc(iso.x, iso.y - 10, 8, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = '#38bdf8';
    ctx.lineWidth = 2;
    ctx.stroke();

    const gunTip = worldToIso(
      turret.x + Math.cos(turret.angle) * 0.7,
      turret.y + Math.sin(turret.angle) * 0.7,
      camera
    );
    ctx.beginPath();
    ctx.moveTo(iso.x, iso.y - 10);
    ctx.lineTo(gunTip.x, gunTip.y - 10);
    ctx.strokeStyle = '#e0f2fe';
    ctx.lineWidth = 3;
    ctx.stroke();
    ctx.restore();
  }

  drawIsoBox(ctx, wall, camera) {
    const hPx =
      wall.type === 'cover'
        ? WALL_HEIGHT_PX * 0.45
        : wall.type === 'ship_ramp'
        ? WALL_HEIGHT_PX * 0.35
        : WALL_HEIGHT_PX;

    const p0 = worldToIso(wall.x, wall.y, camera);
    const p1 = worldToIso(wall.x + wall.w, wall.y, camera);
    const p2 = worldToIso(wall.x + wall.w, wall.y + wall.h, camera);
    const p3 = worldToIso(wall.x, wall.y + wall.h, camera);

    let topColor = '#334155';
    let leftColor = '#1e293b';
    let rightColor = '#0f172a';
    let strokeColor = '#475569';

    if (wall.type === 'door') {
      topColor = '#b45309';
      leftColor = '#92400e';
      rightColor = '#78350f';
      strokeColor = '#f59e0b';
    } else if (wall.type === 'ship_hull') {
      topColor = '#1d4ed8';
      leftColor = '#1e40af';
      rightColor = '#1e3a8a';
      strokeColor = '#60a5fa';
    } else if (wall.type === 'cover') {
      topColor = '#3f3f46';
      leftColor = '#27272a';
      rightColor = '#18181b';
      strokeColor = '#52525b';
    }

    ctx.save();
    // South-West face (p3 -> p2)
    ctx.beginPath();
    ctx.moveTo(p3.x, p3.y);
    ctx.lineTo(p2.x, p2.y);
    ctx.lineTo(p2.x, p2.y - hPx);
    ctx.lineTo(p3.x, p3.y - hPx);
    ctx.closePath();
    ctx.fillStyle = leftColor;
    ctx.fill();
    ctx.strokeStyle = strokeColor;
    ctx.lineWidth = 1;
    ctx.stroke();

    // South-East face (p1 -> p2)
    ctx.beginPath();
    ctx.moveTo(p1.x, p1.y);
    ctx.lineTo(p2.x, p2.y);
    ctx.lineTo(p2.x, p2.y - hPx);
    ctx.lineTo(p1.x, p1.y - hPx);
    ctx.closePath();
    ctx.fillStyle = rightColor;
    ctx.fill();
    ctx.stroke();

    // Top roof face
    ctx.beginPath();
    ctx.moveTo(p0.x, p0.y - hPx);
    ctx.lineTo(p1.x, p1.y - hPx);
    ctx.lineTo(p2.x, p2.y - hPx);
    ctx.lineTo(p3.x, p3.y - hPx);
    ctx.closePath();
    ctx.fillStyle = topColor;
    ctx.fill();
    ctx.stroke();
    ctx.restore();
  }

  drawEnemy(ctx, enemy, camera) {
    const iso = worldToIso(enemy.x, enemy.y, camera);
    ctx.save();

    // Draw enemy sight cone on ground
    const fov = enemy.sight_angle || 1.8;
    const range = Math.min(enemy.sight_range || 10.0, 7.5);
    ctx.beginPath();
    ctx.moveTo(iso.x, iso.y);
    const steps = 14;
    for (let i = 0; i <= steps; i++) {
      const a = enemy.angle - fov * 0.5 + (fov * i) / steps;
      const pt = worldToIso(enemy.x + Math.cos(a) * range, enemy.y + Math.sin(a) * range, camera);
      ctx.lineTo(pt.x, pt.y);
    }
    ctx.closePath();
    ctx.fillStyle =
      enemy.state === 'engaged'
        ? 'rgba(239, 68, 68, 0.16)'
        : enemy.state === 'alerted'
        ? 'rgba(245, 158, 11, 0.14)'
        : 'rgba(148, 163, 184, 0.08)';
    ctx.fill();

    // Shadow
    ctx.beginPath();
    ctx.ellipse(iso.x, iso.y, 13, 7, 0, 0, Math.PI * 2);
    ctx.fillStyle = 'rgba(0,0,0,0.5)';
    ctx.fill();

    // Body
    const rPx = enemy.type === 'brute' ? 15 : 11;
    ctx.beginPath();
    ctx.arc(iso.x, iso.y - 14, rPx, 0, Math.PI * 2);
    ctx.fillStyle = enemy.color || '#ef4444';
    ctx.fill();
    ctx.strokeStyle = enemy.state === 'engaged' ? '#ef4444' : '#f8fafc';
    ctx.lineWidth = 2;
    ctx.stroke();

    // HP Bar
    const hpRatio = Math.max(0, enemy.hp / enemy.max_hp);
    ctx.fillStyle = 'rgba(15, 23, 42, 0.85)';
    ctx.fillRect(iso.x - 16, iso.y - 36, 32, 5);
    ctx.fillStyle = '#ef4444';
    ctx.fillRect(iso.x - 16, iso.y - 36, 32 * hpRatio, 5);

    // State badge (! or ?)
    if (enemy.state !== 'patrol') {
      ctx.font = 'bold 13px monospace';
      ctx.textAlign = 'center';
      ctx.fillStyle = enemy.state === 'engaged' ? '#ef4444' : '#facc15';
      ctx.fillText(enemy.state === 'engaged' ? '!' : '?', iso.x, iso.y - 42);
    }

    ctx.restore();
  }

  drawPlayer(ctx, player, isLocal, mouseWorld, camera) {
    const iso = worldToIso(player.x, player.y, camera);
    const cls = this.classesConfig[player.class_id] || {};
    const color = cls.color || '#38bdf8';

    ctx.save();

    // Ground circle
    ctx.beginPath();
    ctx.ellipse(iso.x, iso.y, 15, 8, 0, 0, Math.PI * 2);
    ctx.fillStyle = 'rgba(0, 0, 0, 0.55)';
    ctx.fill();
    ctx.strokeStyle = isLocal ? '#38bdf8' : color;
    ctx.lineWidth = 2;
    ctx.stroke();

    // Active Vanguard Shockwave Shield visual
    if (player.skill_active === 'shockwave_shield') {
      ctx.beginPath();
      ctx.ellipse(iso.x, iso.y - 12, 26, 16, 0, 0, Math.PI * 2);
      ctx.strokeStyle = '#f87171';
      ctx.lineWidth = 3;
      ctx.stroke();
    }

    // Aim laser line for local player
    if (isLocal && !player.downed && mouseWorld) {
      const mIso = worldToIso(mouseWorld.x, mouseWorld.y, camera);
      ctx.beginPath();
      ctx.moveTo(iso.x, iso.y - 16);
      ctx.lineTo(mIso.x, mIso.y);
      ctx.strokeStyle = 'rgba(56, 189, 248, 0.28)';
      ctx.lineWidth = 1.5;
      ctx.stroke();
    }

    // Character cylinder/torso
    ctx.beginPath();
    ctx.arc(iso.x, iso.y - 16, 12, 0, Math.PI * 2);
    ctx.fillStyle = player.downed ? '#64748b' : color;
    ctx.fill();
    ctx.strokeStyle = '#ffffff';
    ctx.lineWidth = 2;
    ctx.stroke();

    // Gun barrel pointing along player.angle
    const barrelEnd = worldToIso(
      player.x + Math.cos(player.angle) * 0.75,
      player.y + Math.sin(player.angle) * 0.75,
      camera
    );
    ctx.beginPath();
    ctx.moveTo(iso.x, iso.y - 16);
    ctx.lineTo(barrelEnd.x, barrelEnd.y - 16);
    ctx.strokeStyle = '#e2e8f0';
    ctx.lineWidth = 3.5;
    ctx.stroke();

    // Callsign & HP Bar
    const hpRatio = Math.max(0, player.hp / player.max_hp);
    ctx.fillStyle = 'rgba(15, 23, 42, 0.85)';
    ctx.fillRect(iso.x - 20, iso.y - 40, 40, 5);
    ctx.fillStyle = player.downed ? '#ef4444' : '#22c55e';
    ctx.fillRect(iso.x - 20, iso.y - 40, 40 * hpRatio, 5);

    ctx.font = 'bold 11px Inter, sans-serif';
    ctx.textAlign = 'center';
    ctx.fillStyle = '#f8fafc';
    ctx.fillText(player.downed ? `${player.name} [DOWNED]` : player.name, iso.x, iso.y - 45);

    ctx.restore();
  }

  drawProjectiles(ctx, projectiles, camera) {
    for (const proj of projectiles) {
      const iso = worldToIso(proj.x, proj.y, camera);
      const tail = worldToIso(proj.x - proj.vx * 0.03, proj.y - proj.vy * 0.03, camera);
      ctx.save();
      ctx.beginPath();
      ctx.moveTo(tail.x, tail.y - 14);
      ctx.lineTo(iso.x, iso.y - 14);
      ctx.strokeStyle = proj.color || '#facc15';
      ctx.lineWidth = 3;
      ctx.stroke();

      ctx.beginPath();
      ctx.arc(iso.x, iso.y - 14, 3, 0, Math.PI * 2);
      ctx.fillStyle = '#ffffff';
      ctx.fill();
      ctx.restore();
    }
  }

  drawFogOfWar(ctx, map, visions, camera) {
    const W = this.canvas.width;
    const H = this.canvas.height;
    if (this.fogCanvas.width !== W || this.fogCanvas.height !== H) {
      this.fogCanvas.width = W;
      this.fogCanvas.height = H;
    }

    const fctx = this.fogCtx;
    fctx.clearRect(0, 0, W, H);

    // Fill with dark shroud over the entire viewport
    fctx.fillStyle = 'rgba(4, 7, 13, 0.62)';
    fctx.fillRect(0, 0, W, H);

    // Cut out active line-of-sight polygons for all players
    fctx.globalCompositeOperation = 'destination-out';
    for (const v of visions) {
      if (!v.poly || v.poly.length < 3) continue;
      fctx.beginPath();
      const first = worldToIso(v.poly[0].x, v.poly[0].y, camera);
      fctx.moveTo(first.x, first.y - 12);
      for (let i = 1; i < v.poly.length; i++) {
        const pt = worldToIso(v.poly[i].x, v.poly[i].y, camera);
        fctx.lineTo(pt.x, pt.y - 12);
      }
      fctx.closePath();
      fctx.fillStyle = 'rgba(0, 0, 0, 1)';
      fctx.fill();
    }
    fctx.globalCompositeOperation = 'source-over';

    ctx.drawImage(this.fogCanvas, 0, 0);
  }
}
