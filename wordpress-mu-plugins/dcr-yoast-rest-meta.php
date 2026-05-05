<?php
/**
 * Plugin Name: DCReport — Yoast SEO meta REST writable (Scoutmonkeys)
 * Description: Registers Yoast post meta keys with show_in_rest so Application Passwords can persist SEO title, meta description, focus keyphrase, and Open Graph image via wp-json/wp/v2/posts.
 * Version: 1.0.0
 *
 * Install: copy to wp-content/mu-plugins/ on dcreport.org (must load as mu-plugin).
 */
defined('ABSPATH') || exit;

add_action(
    'init',
    static function () {
        $post_types = apply_filters('dcr_scoutmonkeys_yoast_rest_post_types', array('post', 'page'));
        foreach ($post_types as $post_type) {
            if (!post_type_exists($post_type)) {
                continue;
            }
            register_post_meta(
                $post_type,
                '_yoast_wpseo_title',
                array(
                    'single'            => true,
                    'type'              => 'string',
                    'show_in_rest'      => true,
                    'auth_callback'     => static function () {
                        return current_user_can('edit_posts');
                    },
                    'sanitize_callback' => static function ($value) {
                        return is_string($value) ? sanitize_text_field(wp_unslash($value)) : '';
                    },
                )
            );
            register_post_meta(
                $post_type,
                '_yoast_wpseo_metadesc',
                array(
                    'single'            => true,
                    'type'              => 'string',
                    'show_in_rest'      => true,
                    'auth_callback'     => static function () {
                        return current_user_can('edit_posts');
                    },
                    'sanitize_callback' => static function ($value) {
                        return is_string($value) ? sanitize_text_field(wp_unslash($value)) : '';
                    },
                )
            );
            register_post_meta(
                $post_type,
                '_yoast_wpseo_focuskw',
                array(
                    'single'            => true,
                    'type'              => 'string',
                    'show_in_rest'      => true,
                    'auth_callback'     => static function () {
                        return current_user_can('edit_posts');
                    },
                    'sanitize_callback' => static function ($value) {
                        return is_string($value) ? sanitize_text_field(wp_unslash($value)) : '';
                    },
                )
            );
            register_post_meta(
                $post_type,
                '_yoast_wpseo_opengraph-image',
                array(
                    'single'            => true,
                    'type'              => 'string',
                    'show_in_rest'      => true,
                    'auth_callback'     => static function () {
                        return current_user_can('edit_posts');
                    },
                    'sanitize_callback' => static function ($value) {
                        return is_string($value) ? esc_url_raw(wp_unslash($value)) : '';
                    },
                )
            );
        }
    },
    20
);
