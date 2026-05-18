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

add_filter(
    'rest_post_collection_params',
    static function ( $params ) {
        $params['yoast_og_filename'] = array(
            'description'       => 'Filter posts whose Yoast OG image URL contains this filename (Scoutmonkeys).',
            'type'              => 'string',
            'sanitize_callback' => 'sanitize_text_field',
        );
        return $params;
    }
);

add_filter(
    'rest_post_query',
    static function ( $args, $request ) {
        $filename = (string) $request->get_param( 'yoast_og_filename' );
        if ( $filename === '' ) {
            return $args;
        }
        $args['meta_query'] = array(
            array(
                'key'     => '_yoast_wpseo_opengraph-image',
                'value'   => $filename,
                'compare' => 'LIKE',
            ),
        );
        return $args;
    },
    10,
    2
);

add_action(
    'rest_api_init',
    static function () {
        register_rest_route(
            'scoutmonkeys/v1',
            '/posts-by-yoast-og-filename',
            array(
                'methods'             => 'GET',
                'permission_callback' => static function () {
                    return current_user_can( 'edit_posts' );
                },
                'args'                => array(
                    'filename'  => array(
                        'required'          => true,
                        'type'              => 'string',
                        'sanitize_callback' => 'sanitize_text_field',
                    ),
                    'page'      => array(
                        'default'           => 1,
                        'type'              => 'integer',
                        'sanitize_callback' => 'absint',
                    ),
                    'per_page'  => array(
                        'default'           => 100,
                        'type'              => 'integer',
                        'sanitize_callback' => 'absint',
                    ),
                ),
                'callback'            => static function ( $request ) {
                    $filename = (string) $request->get_param( 'filename' );
                    $page     = max( 1, (int) $request->get_param( 'page' ) );
                    $per_page = min( 100, max( 1, (int) $request->get_param( 'per_page' ) ) );
                    $query    = new WP_Query(
                        array(
                            'post_type'      => 'post',
                            'post_status'    => array( 'publish', 'draft', 'pending', 'private', 'future' ),
                            'posts_per_page' => $per_page,
                            'paged'          => $page,
                            'meta_query'     => array(
                                array(
                                    'key'     => '_yoast_wpseo_opengraph-image',
                                    'value'   => $filename,
                                    'compare' => 'LIKE',
                                ),
                            ),
                        )
                    );
                    $rows = array();
                    foreach ( $query->posts as $post ) {
                        $rows[] = array(
                            'id'                    => (int) $post->ID,
                            'status'                => $post->post_status,
                            'date'                  => $post->post_date,
                            'title'                 => array( 'raw' => $post->post_title ),
                            'yoast_opengraph_image' => (string) get_post_meta( $post->ID, '_yoast_wpseo_opengraph-image', true ),
                        );
                    }
                    return new WP_REST_Response(
                        array(
                            'posts'       => $rows,
                            'total'       => (int) $query->found_posts,
                            'total_pages' => (int) $query->max_num_pages,
                            'page'        => $page,
                        ),
                        200
                    );
                },
            )
        );
    }
);
