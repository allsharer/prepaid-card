-- First, insert the companies (both payees and payers)
BEGIN TRANSACTION;

-- Insert Payees
INSERT INTO companies (name, type) VALUES 
('Unknown', 'Payee'),
('Beverly', 'Payee'),
('JPS', 'Payee'),
('NWC', 'Payee'),
('Flow', 'Payee'),
('Amazon', 'Payee'),
('AliExpress', 'Payee'),
('Zoom', 'Payee'),
('Island Grill', 'Payee'),
('Sundries OTP', 'Payee');

-- Insert Payers
INSERT INTO companies (name, type) VALUES 
('Beverly', 'Payer'),
('BE Deposit', 'Payer');

-- Now insert all the transactions
-- Note: We need to lookup the company refnos based on name and type
INSERT INTO account (date, type, company, details, amount) VALUES
('2025-07-03', 'Receipt', 
  (SELECT refno FROM companies WHERE name='Beverly' AND type='Payer'), 
  'Transfer for cash given', 30000.00),
  
('2025-07-06', 'Payment', 
  (SELECT refno FROM companies WHERE name='Flow' AND type='Payee'), 
  'Internet and phone', 6000.00),
  
('2025-07-06', 'Payment', 
  (SELECT refno FROM companies WHERE name='NWC' AND type='Payee'), 
  'Water charges', 5148.98),
  
('2025-07-06', 'Payment', 
  (SELECT refno FROM companies WHERE name='JPS' AND type='Payee'), 
  'Electricity charges', 2200.73),
  
('2025-07-10', 'Payment', 
  (SELECT refno FROM companies WHERE name='Beverly' AND type='Payee'), 
  'Inverter purchase', 30800.00),
  
('2025-07-15', 'Payment', 
  (SELECT refno FROM companies WHERE name='Beverly' AND type='Payee'), 
  'Goods purchased', 1100.00),
  
('2025-07-19', 'Payment', 
  (SELECT refno FROM companies WHERE name='Unknown' AND type='Payee'), 
  'Carr and Company Solicitor UK', 767.70),
  
('2025-07-19', 'Payment', 
  (SELECT refno FROM companies WHERE name='Unknown' AND type='Payee'), 
  'Carr and Company Solicitor UK', 568.10),
  
('2025-07-25', 'Receipt', 
  (SELECT refno FROM companies WHERE name='Beverly' AND type='Payer'), 
  'Transfer for cash given', 15000.00),
  
('2025-07-25', 'Payment', 
  (SELECT refno FROM companies WHERE name='Zoom' AND type='Payee'), 
  'Payment_Jul-Aug YY24Z2', 2899.86);

COMMIT;

-- Verify the inserted data
SELECT 'Companies:' AS '';
SELECT * FROM companies;

SELECT 'Transactions:' AS '';
SELECT a.refno, a.date, a.type, c.name AS company, a.details, a.amount 
FROM account a JOIN companies c ON a.company = c.refno
ORDER BY a.date;
