-- Reference source schema for the NL2Data mainflow demo.
-- Domain: order-fulfillment analytics with cross-table business signals.
-- Tables are created in the default (public) schema so the demo adapter can
-- resolve them without relying on a custom search_path.

CREATE TABLE IF NOT EXISTS customers (
    customer_id BIGINT PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL,
    region VARCHAR(64) NOT NULL,
    channel VARCHAR(64) NOT NULL,
    email VARCHAR(256) NOT NULL,  -- governance-sensitive field
    created_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS products (
    product_id BIGINT PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL,
    category VARCHAR(128) NOT NULL,
    unit_price NUMERIC(12, 2) NOT NULL,
    stock_quantity INT NOT NULL,
    created_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    order_id BIGINT PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL,
    customer_id BIGINT NOT NULL REFERENCES customers(customer_id),
    region VARCHAR(64) NOT NULL,
    channel VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL,  -- placed, paid, shipped, cancelled, refunded
    created_at TIMESTAMP NOT NULL,
    paid_at TIMESTAMP,
    shipped_at TIMESTAMP,
    refunded_at TIMESTAMP,
    amount NUMERIC(12, 2)
);

CREATE TABLE IF NOT EXISTS order_items (
    item_id BIGINT PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL,
    order_id BIGINT NOT NULL REFERENCES orders(order_id),
    product_id BIGINT NOT NULL REFERENCES products(product_id),
    quantity INT NOT NULL,
    unit_price NUMERIC(12, 2) NOT NULL,
    created_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS payments (
    payment_id BIGINT PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL,
    order_id BIGINT NOT NULL REFERENCES orders(order_id),
    amount NUMERIC(12, 2) NOT NULL,
    status VARCHAR(32) NOT NULL,  -- completed, failed, refunded
    created_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS shipments (
    shipment_id BIGINT PRIMARY KEY,
    tenant_id VARCHAR(64) NOT NULL,
    order_id BIGINT NOT NULL REFERENCES orders(order_id),
    shipped_at TIMESTAMP,
    delivered_at TIMESTAMP,
    status VARCHAR(32) NOT NULL,  -- pending, shipped, partial, delivered
    created_at TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_orders_tenant ON orders(tenant_id);
CREATE INDEX IF NOT EXISTS idx_orders_created ON orders(created_at);
CREATE INDEX IF NOT EXISTS idx_payments_order ON payments(order_id);
CREATE INDEX IF NOT EXISTS idx_shipments_order ON shipments(order_id);
