-- Remove unsafe cross-user position links while preserving symbol-level alerts.
UPDATE qd_position_alerts AS alert
SET position_id = NULL,
    updated_at = NOW()
WHERE alert.position_id IS NOT NULL
  AND NOT EXISTS (
      SELECT 1
      FROM qd_manual_positions AS owned_position
      WHERE owned_position.id = alert.position_id
        AND owned_position.user_id = alert.user_id
  );

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'uq_manual_positions_id_user'
          AND conrelid = 'qd_manual_positions'::regclass
    ) THEN
        ALTER TABLE qd_manual_positions
        ADD CONSTRAINT uq_manual_positions_id_user UNIQUE (id, user_id);
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fk_position_alerts_owned_position'
          AND conrelid = 'qd_position_alerts'::regclass
    ) THEN
        ALTER TABLE qd_position_alerts
        ADD CONSTRAINT fk_position_alerts_owned_position
        FOREIGN KEY (position_id, user_id)
        REFERENCES qd_manual_positions(id, user_id)
        ON DELETE CASCADE;
    END IF;
END $$;
