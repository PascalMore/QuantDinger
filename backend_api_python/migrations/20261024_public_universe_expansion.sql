INSERT INTO qd_universes
  (code, name, name_i18n_key, market, universe_type, source, source_ref, is_system, status)
VALUES
  ('sse50', 'SSE 50', 'universe.catalog.sse50', 'CNStock', 'index', 'public_snapshot', '000016.SH', TRUE, 'data_required'),
  ('csi800', 'CSI 800', 'universe.catalog.csi800', 'CNStock', 'index', 'public_snapshot', '000906.SH', TRUE, 'data_required'),
  ('csi1000', 'CSI 1000', 'universe.catalog.csi1000', 'CNStock', 'index', 'public_snapshot', '000852.SH', TRUE, 'data_required'),
  ('dow30', 'Dow Jones 30', 'universe.catalog.dow30', 'USStock', 'index', 'public_snapshot', 'DJIA', TRUE, 'data_required'),
  ('sp400', 'S&P MidCap 400', 'universe.catalog.sp400', 'USStock', 'index', 'public_snapshot', 'SP400', TRUE, 'data_required'),
  ('sp600', 'S&P SmallCap 600', 'universe.catalog.sp600', 'USStock', 'index', 'public_snapshot', 'SP600', TRUE, 'data_required'),
  ('russell1000', 'Russell 1000', 'universe.catalog.russell1000', 'USStock', 'index', 'public_snapshot', 'RUI', TRUE, 'data_required'),
  ('hk_hsi', 'Hang Seng Index', 'universe.catalog.hkHsi', 'HKStock', 'index', 'public_snapshot', 'HSI', TRUE, 'data_required'),
  ('hk_stock_connect', 'Hong Kong Stock Connect', 'universe.catalog.hkStockConnect', 'HKStock', 'market', 'public_snapshot', 'HK_STOCK_CONNECT', TRUE, 'data_required')
ON CONFLICT DO NOTHING;

INSERT INTO qd_universes
  (code, name, name_i18n_key, market, universe_type, source, source_ref, is_system, status)
VALUES
  ('us_sector_information_technology', 'US Information Technology', 'universe.catalog.usSectorInformationTechnology', 'USStock', 'industry', 'public_snapshot', 'sp500:GICS Sector:Information Technology', TRUE, 'data_required'),
  ('us_sector_health_care', 'US Health Care', 'universe.catalog.usSectorHealthCare', 'USStock', 'industry', 'public_snapshot', 'sp500:GICS Sector:Health Care', TRUE, 'data_required'),
  ('us_sector_financials', 'US Financials', 'universe.catalog.usSectorFinancials', 'USStock', 'industry', 'public_snapshot', 'sp500:GICS Sector:Financials', TRUE, 'data_required'),
  ('us_sector_consumer_discretionary', 'US Consumer Discretionary', 'universe.catalog.usSectorConsumerDiscretionary', 'USStock', 'industry', 'public_snapshot', 'sp500:GICS Sector:Consumer Discretionary', TRUE, 'data_required'),
  ('us_sector_communication_services', 'US Communication Services', 'universe.catalog.usSectorCommunicationServices', 'USStock', 'industry', 'public_snapshot', 'sp500:GICS Sector:Communication Services', TRUE, 'data_required'),
  ('us_sector_industrials', 'US Industrials', 'universe.catalog.usSectorIndustrials', 'USStock', 'industry', 'public_snapshot', 'sp500:GICS Sector:Industrials', TRUE, 'data_required'),
  ('us_sector_consumer_staples', 'US Consumer Staples', 'universe.catalog.usSectorConsumerStaples', 'USStock', 'industry', 'public_snapshot', 'sp500:GICS Sector:Consumer Staples', TRUE, 'data_required'),
  ('us_sector_energy', 'US Energy', 'universe.catalog.usSectorEnergy', 'USStock', 'industry', 'public_snapshot', 'sp500:GICS Sector:Energy', TRUE, 'data_required'),
  ('us_sector_utilities', 'US Utilities', 'universe.catalog.usSectorUtilities', 'USStock', 'industry', 'public_snapshot', 'sp500:GICS Sector:Utilities', TRUE, 'data_required'),
  ('us_sector_real_estate', 'US Real Estate', 'universe.catalog.usSectorRealEstate', 'USStock', 'industry', 'public_snapshot', 'sp500:GICS Sector:Real Estate', TRUE, 'data_required'),
  ('us_sector_materials', 'US Materials', 'universe.catalog.usSectorMaterials', 'USStock', 'industry', 'public_snapshot', 'sp500:GICS Sector:Materials', TRUE, 'data_required')
ON CONFLICT DO NOTHING;

UPDATE qd_universes SET status = 'deprecated', updated_at = NOW()
WHERE code IN ('hk_hsi_core50', 'hk_high_dividend50') AND is_system = TRUE
RETURNING code;
